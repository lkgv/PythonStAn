# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-25T23:27:24.473194

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 29.47 seconds
- **Modules analyzed**: 65
- **Modules succeeded**: 61
- **Modules failed**: 4
- **Success rate**: 93.8%
- **Throughput**: 752.9 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 2099
- **Non-empty points-to sets**: 2099
- **Singleton sets**: 1708 (81.4%)
- **Empty sets**: 0
- **Average set size**: 1.42
- **Maximum set size**: 30
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 1708 |
| 11-20 | 19 |
| 2-5 | 348 |
| 21-50 | 1 |
| 6-10 | 23 |

## Call Graph Metrics

- **Total functions**: 1058
- **Total call edges**: 136
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 261
- **Classes with MRO**: 261
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 1435
- **Average objects per variable**: 1.42
- **Variables with no objects**: 0
- **Variables with singleton**: 1708
- **Variables with multiple objects**: 391

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 1123 |
| class | 235 |
| func | 77 |

## Function Metrics

- **Total functions tracked**: 70

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Contexts |
|----------|------------|-----------|----------|
| _force_correct_text_stream | 1 | 1 | 3 |
| get_best_encoding | 1 | 1 | 6 |
| _is_binary_writer | 1 | 1 | 7 |
| _make_text_stream | 1 | 1 | 6 |
| get_filesystem_encoding | 1 | 1 | 2 |
| _is_binary_reader | 1 | 1 | 4 |
| isatty | 1 | 1 | 2 |
| _is_compat_stream_attr | 1 | 1 | 2 |
| strip_ansi | 1 | 1 | 2 |
| _get_windows_console_stream | 1 | 1 | 4 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Variables |
|----------|-----------|------------|-----------|
| _force_correct_text_stream | 1 | 1 | 4 |
| get_best_encoding | 1 | 1 | 0 |
| _is_binary_writer | 1 | 1 | 1 |
| _make_text_stream | 1 | 1 | 6 |
| get_filesystem_encoding | 1 | 1 | 0 |
| _is_binary_reader | 1 | 1 | 1 |
| isatty | 1 | 1 | 1 |
| _is_compat_stream_attr | 1 | 1 | 0 |
| strip_ansi | 1 | 1 | 0 |
| _get_windows_console_stream | 1 | 1 | 0 |

## Memory Usage

- **Peak memory**: 117.50 MB
- **Current memory**: 109.83 MB

## Error Analysis

| Error Type | Count | Affected Modules |
|-----------|-------|------------------|
| AssertionError | 4 | site-packages/jinja2/async_utils.py, site-packages/jinja2/environment.py, site-packages/jinja2/filters.py (+1 more) |

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| site-packages/click/__init__.py | 0.01 | 0 | 72 |
| site-packages/click/_compat.py | 0.56 | 51 | 460 |
| site-packages/click/_termui_impl.py | 1.00 | 33 | 533 |
| site-packages/click/_textwrap.py | 0.09 | 3 | 39 |
| site-packages/click/_winconsole.py | 0.13 | 20 | 215 |
| site-packages/click/core.py | 2.77 | 131 | 2272 |
| site-packages/click/decorators.py | 0.39 | 18 | 376 |
| site-packages/click/exceptions.py | 0.29 | 18 | 219 |
| site-packages/click/formatting.py | 0.35 | 16 | 246 |
| site-packages/click/globals.py | 0.08 | 6 | 47 |
| site-packages/click/parser.py | 0.61 | 20 | 369 |
| site-packages/click/shell_completion.py | 0.34 | 26 | 438 |
| site-packages/click/termui.py | 0.46 | 17 | 635 |
| site-packages/click/testing.py | 0.49 | 25 | 386 |
| site-packages/click/types.py | 1.03 | 61 | 796 |
| site-packages/click/utils.py | 0.37 | 30 | 436 |
| site-packages/jinja2/__init__.py | 0.01 | 0 | 36 |
| site-packages/jinja2/_identifier.py | 0.01 | 0 | 4 |
| site-packages/jinja2/bccache.py | 0.35 | 24 | 309 |
| site-packages/jinja2/compiler.py | 3.32 | 111 | 1533 |

*...and 41 more modules*

## Failed Modules

| Module | Error Type | Error Message |
|--------|-----------|---------------|
| site-packages/jinja2/async_utils.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/environment.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/filters.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/runtime.py | AssertionError | The test variable of IfEdge should be ast.Name! |