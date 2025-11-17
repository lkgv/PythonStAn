# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T00:16:49.929321

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 30.33 seconds
- **Modules analyzed**: 69
- **Modules succeeded**: 65
- **Modules failed**: 4
- **Success rate**: 94.2%
- **Throughput**: 754.3 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 2155
- **Non-empty points-to sets**: 2155
- **Singleton sets**: 1753 (81.3%)
- **Empty sets**: 0
- **Average set size**: 1.42
- **Maximum set size**: 30
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 1753 |
| 11-20 | 19 |
| 2-5 | 359 |
| 21-50 | 1 |
| 6-10 | 23 |

## Call Graph Metrics

- **Total functions**: 1098
- **Total call edges**: 137
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 270
- **Classes with MRO**: 270
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 1470
- **Average objects per variable**: 1.42
- **Variables with no objects**: 0
- **Variables with singleton**: 1753
- **Variables with multiple objects**: 402

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 1150 |
| class | 241 |
| func | 79 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.527
- **Functions with no calls**: 805
- **Functions with no objects**: 835
- **Average objects per function**: 0.2
- **Average calls per function**: 0.2
- **Max objects in a function**: 18
- **Max calls in a function**: 2

**Interpretation**: strong correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 876

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 805 (91.9%) |
| 1 call | 71 (8.1%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 835 (95.3%) |
| 1-10 objects | 37 (4.2%) |
| 11-50 objects | 4 (0.5%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| callable_reference | 1 | 1 | 1 | 2 |
| get_filesystem_encoding | 1 | 1 | 0 | 2 |
| _make_text_stream | 1 | 1 | 3 | 6 |
| is_ascii_encoding | 1 | 1 | 0 | 6 |
| get_best_encoding | 1 | 1 | 0 | 6 |
| _is_binary_reader | 1 | 1 | 1 | 4 |
| _is_binary_writer | 1 | 1 | 1 | 7 |
| _find_binary_reader | 1 | 1 | 0 | 2 |
| _find_binary_writer | 1 | 1 | 0 | 5 |
| _is_compat_stream_attr | 1 | 1 | 0 | 2 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| callable_reference | 1 | 1 | 1 | 3 |
| get_filesystem_encoding | 1 | 1 | 0 | 0 |
| _make_text_stream | 1 | 1 | 3 | 6 |
| is_ascii_encoding | 1 | 1 | 0 | 0 |
| get_best_encoding | 1 | 1 | 0 | 0 |
| _is_binary_reader | 1 | 1 | 1 | 1 |
| _is_binary_writer | 1 | 1 | 1 | 1 |
| _find_binary_reader | 1 | 1 | 0 | 0 |
| _find_binary_writer | 1 | 1 | 0 | 0 |
| _is_compat_stream_attr | 1 | 1 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| get_template_locals | 18 | 1 | 1 | 12 |
| next | 17 | 1 | 1 | 27 |
| compile_rules | 13 | 1 | 1 | 27 |
| _unpack_args | 12 | 1 | 1 | 19 |
| style | 10 | 1 | 1 | 19 |
| measure_table | 9 | 1 | 1 | 10 |
| convert_type | 8 | 1 | 1 | 12 |
| find_undeclared | 6 | 1 | 1 | 8 |
| getattr | 6 | 1 | 1 | 16 |
| fake_traceback | 5 | 1 | 1 | 6 |

## Memory Usage

- **Peak memory**: 120.39 MB
- **Current memory**: 112.79 MB

## Error Analysis

| Error Type | Count | Affected Modules |
|-----------|-------|------------------|
| AssertionError | 4 | site-packages/jinja2/async_utils.py, site-packages/jinja2/environment.py, site-packages/jinja2/filters.py (+1 more) |

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| site-packages/blinker/__init__.py | 0.00 | 0 | 17 |
| site-packages/blinker/_saferef.py | 0.17 | 9 | 159 |
| site-packages/blinker/_utilities.py | 0.13 | 10 | 97 |
| site-packages/blinker/base.py | 0.42 | 23 | 421 |
| site-packages/click/__init__.py | 0.02 | 0 | 72 |
| site-packages/click/_compat.py | 0.55 | 51 | 460 |
| site-packages/click/_termui_impl.py | 1.02 | 33 | 533 |
| site-packages/click/_textwrap.py | 0.09 | 3 | 39 |
| site-packages/click/_winconsole.py | 0.13 | 20 | 215 |
| site-packages/click/core.py | 2.81 | 131 | 2272 |
| site-packages/click/decorators.py | 0.38 | 18 | 376 |
| site-packages/click/exceptions.py | 0.29 | 18 | 219 |
| site-packages/click/formatting.py | 0.34 | 16 | 246 |
| site-packages/click/globals.py | 0.09 | 6 | 47 |
| site-packages/click/parser.py | 0.61 | 20 | 369 |
| site-packages/click/shell_completion.py | 0.33 | 26 | 438 |
| site-packages/click/termui.py | 0.52 | 17 | 635 |
| site-packages/click/testing.py | 0.47 | 25 | 386 |
| site-packages/click/types.py | 0.96 | 61 | 796 |
| site-packages/click/utils.py | 0.37 | 30 | 436 |

*...and 45 more modules*

## Failed Modules

| Module | Error Type | Error Message |
|--------|-----------|---------------|
| site-packages/jinja2/async_utils.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/environment.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/filters.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/runtime.py | AssertionError | The test variable of IfEdge should be ast.Name! |