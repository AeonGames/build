#!/usr/bin/env python

# Copyright 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Aggregates Jacoco coverage files to produce output."""

from __future__ import print_function

import argparse
import fnmatch
import json
import os
import sys

import devil_chromium
from devil.utils import cmd_helper
from pylib.constants import host_paths

# Source paths should be passed to Jacoco in a way that the relative file paths
# reflect the class package name.
_PARTIAL_PACKAGE_NAMES = ['com/google', 'org/chromium']

# The sources_json_file is generated by jacoco_instr.py with source directories
# and input path to non-instrumented jars.
# e.g.
# 'source_dirs': [
#   "chrome/android/java/src/org/chromium/chrome/browser/toolbar/bottom",
#   "chrome/android/java/src/org/chromium/chrome/browser/ui/system",
# ...]
# 'input_path':
#   '$CHROMIUM_OUTPUT_DIR/\
#    obj/chrome/android/features/tab_ui/java__process_prebuilt-filtered.jar'

_SOURCES_JSON_FILES_SUFFIX = '__jacoco_sources.json'

# These should match the jar class files generated in internal_rules.gni
_DEVICE_CLASS_EXCLUDE_SUFFIX = 'host_filter.jar'
_HOST_CLASS_EXCLUDE_SUFFIX = 'device_filter.jar'


def _CreateClassfileArgs(class_files, exclude_suffix=None):
  """Returns a list of files that don't have a given suffix.

  Args:
    class_files: A list of class files.
    exclude_suffix: Suffix to look for to exclude.

  Returns:
    A list of files that don't use the suffix.
  """
  result_class_files = []
  for f in class_files:
    if exclude_suffix:
      if not f.endswith(exclude_suffix):
        result_class_files += ['--classfiles', f]
    else:
      result_class_files += ['--classfiles', f]

  return result_class_files


def _GenerateReportOutputArgs(args, class_files, report_type):
  class_jar_exclude = None
  if report_type == 'device':
    class_jar_exclude = _DEVICE_CLASS_EXCLUDE_SUFFIX
  elif report_type == 'host':
    class_jar_exclude = _HOST_CLASS_EXCLUDE_SUFFIX

  cmd = _CreateClassfileArgs(class_files, class_jar_exclude)
  if args.format == 'html':
    report_dir = os.path.join(args.output_dir, report_type)
    if not os.path.exists(report_dir):
      os.makedirs(report_dir)
    cmd += ['--html', report_dir]
  elif args.format == 'xml':
    cmd += ['--xml', args.output_file]
  elif args.format == 'csv':
    cmd += ['--csv', args.output_file]

  return cmd


def _GetFilesWithSuffix(root_dir, suffix):
  """Gets all files with a given suffix.

  Args:
    root_dir: Directory in which to search for files.
    suffix: Suffix to look for.

  Returns:
    A list of absolute paths to files that match.
  """
  files = []
  for root, _, filenames in os.walk(root_dir):
    basenames = fnmatch.filter(filenames, '*' + suffix)
    files.extend([os.path.join(root, basename) for basename in basenames])

  return files


def _ParseArguments(parser):
  """Parses the command line arguments.

  Args:
    parser: ArgumentParser object.

  Returns:
    The parsed arguments.
  """
  parser.add_argument(
      '--format',
      required=True,
      choices=['html', 'xml', 'csv'],
      help='Output report format. Choose one from html, xml and csv.')
  parser.add_argument(
      '--device-or-host',
      choices=['device', 'host'],
      help='Selection on whether to use the device classpath files or the '
      'host classpath files. Host would typically be used for junit tests '
      ' and device for tests that run on the device. Only used for xml and csv'
      ' reports.')
  parser.add_argument('--output-dir', help='html report output directory.')
  parser.add_argument('--output-file',
                      help='xml file to write device coverage results.')
  parser.add_argument(
      '--coverage-dir',
      required=True,
      help='Root of the directory in which to search for '
      'coverage data (.exec) files.')
  parser.add_argument(
      '--sources-json-dir',
      help='Root of the directory in which to search for '
      '*__jacoco_sources.json files.')
  parser.add_argument(
      '--class-files',
      nargs='+',
      help='Location of Java non-instrumented class files. '
      'Use non-instrumented jars instead of instrumented jars. '
      'e.g. use chrome_java__process_prebuilt_(host/device)_filter.jar instead'
      'of chrome_java__process_prebuilt-instrumented.jar')
  parser.add_argument(
      '--sources',
      nargs='+',
      help='Location of the source files. '
      'Specified source folders must be the direct parent of the folders '
      'that define the Java packages.'
      'e.g. <src_dir>/chrome/android/java/src/')
  parser.add_argument(
      '--cleanup',
      action='store_true',
      help='If set, removes coverage files generated at '
      'runtime.')
  args = parser.parse_args()

  if args.format == 'html' and not args.output_dir:
    parser.error('--output-dir needed for report.')
  if args.format in ('csv', 'xml'):
    if not args.output_file:
      parser.error('--output-file needed for xml/csv reports.')
    if not args.device_or_host and args.sources_json_dir:
      parser.error('--device-or-host selection needed with --sources-json-dir')

  if not (args.sources_json_dir or args.class_files):
    parser.error('At least either --sources-json-dir or --class-files needed.')

  return args


def main():
  parser = argparse.ArgumentParser()
  args = _ParseArguments(parser)

  devil_chromium.Initialize()

  coverage_files = _GetFilesWithSuffix(args.coverage_dir, '.exec')
  if not coverage_files:
    parser.error('No coverage file found under %s' % args.coverage_dir)
  print('Found coverage files: %s' % str(coverage_files))

  class_files = []
  source_dirs = []
  if args.sources_json_dir:
    sources_json_files = _GetFilesWithSuffix(args.sources_json_dir,
                                             _SOURCES_JSON_FILES_SUFFIX)
    for f in sources_json_files:
      with open(f, 'r') as json_file:
        data = json.load(json_file)
        class_files.extend(data['input_path'])
        source_dirs.extend(data['source_dirs'])

  # Fix source directories as direct parent of Java packages.
  fixed_source_dirs = set()
  for path in source_dirs:
    for partial in _PARTIAL_PACKAGE_NAMES:
      if partial in path:
        fixed_dir = os.path.join(host_paths.DIR_SOURCE_ROOT,
                                 path[:path.index(partial)])
        fixed_source_dirs.add(fixed_dir)
        break

  if args.class_files:
    class_files += args.class_files
  if args.sources:
    fixed_source_dirs.update(args.sources)

  cmd = [
      'java', '-jar',
      os.path.join(host_paths.DIR_SOURCE_ROOT, 'third_party', 'jacoco', 'lib',
                   'jacococli.jar'), 'report'
  ] + coverage_files

  for source in fixed_source_dirs:
    cmd += ['--sourcefiles', source]

  if args.format == 'html':
    # Both reports are generated for html as the cq bot generates an html
    # report and we wouldn't know which one a developer needed.
    device_cmd = cmd + _GenerateReportOutputArgs(args, class_files, 'device')
    host_cmd = cmd + _GenerateReportOutputArgs(args, class_files, 'host')
    device_exit_code = cmd_helper.RunCmd(device_cmd)
    host_exit_code = cmd_helper.RunCmd(host_cmd)
    exit_code = device_exit_code or host_exit_code
  else:
    cmd = cmd + _GenerateReportOutputArgs(args, class_files,
                                          args.device_or_host)
    exit_code = cmd_helper.RunCmd(cmd)

  if args.cleanup:
    for f in coverage_files:
      os.remove(f)

  # Command tends to exit with status 0 when it actually failed.
  if not exit_code:
    if args.format == 'html':
      if not os.path.isdir(args.output_dir) or not os.listdir(args.output_dir):
        print('No report generated at %s' % args.output_dir)
        exit_code = 1
    elif not os.path.isfile(args.output_file):
      print('No device coverage report generated at %s' % args.output_file)
      exit_code = 1

  return exit_code


if __name__ == '__main__':
  sys.exit(main())
