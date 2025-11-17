# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-25T23:26:17.946967

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 16.01 seconds
- **Modules analyzed**: 30
- **Modules succeeded**: 27
- **Modules failed**: 3
- **Success rate**: 90.0%
- **Throughput**: 706.4 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 1172
- **Non-empty points-to sets**: 1172
- **Singleton sets**: 946 (80.7%)
- **Empty sets**: 0
- **Average set size**: 1.51
- **Maximum set size**: 30
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 946 |
| 11-20 | 16 |
| 2-5 | 196 |
| 21-50 | 1 |
| 6-10 | 13 |

## Call Graph Metrics

- **Total functions**: 540
- **Total call edges**: 82
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 106
- **Classes with MRO**: 106
- **Maximum MRO length**: 3
- **Average MRO length**: 2.43

## Object Metrics

- **Total objects created**: 809
- **Average objects per variable**: 1.51
- **Variables with no objects**: 0
- **Variables with singleton**: 946
- **Variables with multiple objects**: 226

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 665 |
| class | 98 |
| func | 46 |

## Function Metrics

- **Total functions tracked**: 47

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Contexts |
|----------|------------|-----------|----------|
| _is_compat_stream_attr | 1 | 1 | 2 |
| get_filesystem_encoding | 1 | 1 | 2 |
| strip_ansi | 1 | 1 | 2 |
| _is_binary_reader | 1 | 1 | 4 |
| _find_binary_reader | 1 | 1 | 2 |
| _make_text_stream | 1 | 1 | 6 |
| get_binary_stdout | 1 | 1 | 2 |
| _force_correct_text_stream | 1 | 1 | 3 |
| isatty | 1 | 1 | 2 |
| _get_windows_console_stream | 1 | 1 | 4 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Variables |
|----------|-----------|------------|-----------|
| _is_compat_stream_attr | 1 | 1 | 0 |
| get_filesystem_encoding | 1 | 1 | 0 |
| strip_ansi | 1 | 1 | 0 |
| _is_binary_reader | 1 | 1 | 1 |
| _find_binary_reader | 1 | 1 | 0 |
| _make_text_stream | 1 | 1 | 6 |
| get_binary_stdout | 1 | 1 | 0 |
| _force_correct_text_stream | 1 | 1 | 4 |
| isatty | 1 | 1 | 1 |
| _get_windows_console_stream | 1 | 1 | 0 |

## Memory Usage

- **Peak memory**: 72.45 MB
- **Current memory**: 67.61 MB

## Error Analysis

| Error Type | Count | Affected Modules |
|-----------|-------|------------------|
| AssertionError | 3 | site-packages/jinja2/async_utils.py, site-packages/jinja2/environment.py, site-packages/jinja2/filters.py |

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| site-packages/click/__init__.py | 0.01 | 0 | 72 |
| site-packages/click/_compat.py | 0.54 | 51 | 460 |
| site-packages/click/_termui_impl.py | 0.98 | 33 | 533 |
| site-packages/click/_textwrap.py | 0.09 | 3 | 39 |
| site-packages/click/_winconsole.py | 0.13 | 20 | 215 |
| site-packages/click/core.py | 2.76 | 131 | 2272 |
| site-packages/click/decorators.py | 0.39 | 18 | 376 |
| site-packages/click/exceptions.py | 0.28 | 18 | 219 |
| site-packages/click/formatting.py | 0.35 | 16 | 246 |
| site-packages/click/globals.py | 0.09 | 6 | 47 |
| site-packages/click/parser.py | 0.60 | 20 | 369 |
| site-packages/click/shell_completion.py | 0.34 | 26 | 438 |
| site-packages/click/termui.py | 0.48 | 17 | 635 |
| site-packages/click/testing.py | 0.49 | 25 | 386 |
| site-packages/click/types.py | 1.04 | 61 | 796 |
| site-packages/click/utils.py | 0.38 | 30 | 436 |
| site-packages/jinja2/__init__.py | 0.01 | 0 | 36 |
| site-packages/jinja2/_identifier.py | 0.01 | 0 | 4 |
| site-packages/jinja2/bccache.py | 0.35 | 24 | 309 |
| site-packages/jinja2/compiler.py | 3.35 | 111 | 1533 |

*...and 7 more modules*

## Failed Modules

| Module | Error Type | Error Message |
|--------|-----------|---------------|
| site-packages/jinja2/async_utils.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/environment.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/filters.py | AssertionError | The test variable of IfEdge should be ast.Name! |