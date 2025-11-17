# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T00:40:20.016679

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 31.91 seconds
- **Modules analyzed**: 65
- **Modules succeeded**: 61
- **Modules failed**: 4
- **Success rate**: 93.8%
- **Throughput**: 695.4 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 2877
- **Non-empty points-to sets**: 2877
- **Singleton sets**: 2428 (84.4%)
- **Empty sets**: 0
- **Average set size**: 1.35
- **Maximum set size**: 30
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 2428 |
| 11-20 | 20 |
| 2-5 | 395 |
| 21-50 | 1 |
| 6-10 | 33 |

## Call Graph Metrics

- **Total functions**: 81456
- **Total call edges**: 494
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 261
- **Classes with MRO**: 261
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 1702
- **Average objects per variable**: 1.35
- **Variables with no objects**: 0
- **Variables with singleton**: 2428
- **Variables with multiple objects**: 449

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 1390 |
| class | 235 |
| func | 77 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.447
- **Functions with no calls**: 2051
- **Functions with no objects**: 2114
- **Average objects per function**: 0.1
- **Average calls per function**: 0.1
- **Max objects in a function**: 22
- **Max calls in a function**: 2

**Interpretation**: moderate correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 2169

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 2051 (94.6%) |
| 1 call | 118 (5.4%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 2114 (97.5%) |
| 1-10 objects | 50 (2.3%) |
| 11-50 objects | 5 (0.2%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__.get_filesystem_encoding | 1 | 1 | 0 | 2 |
| __main__.get_best_encoding | 1 | 1 | 0 | 3 |
| __main__._find_binary_reader | 1 | 1 | 0 | 4 |
| __main__.get_text_stderr | 1 | 1 | 0 | 2 |
| __main__.open_stream | 1 | 1 | 0 | 2 |
| __main__.strip_ansi | 1 | 1 | 0 | 2 |
| __main__.term_len | 1 | 1 | 0 | 6 |
| __main__.isatty | 1 | 1 | 0 | 3 |
| __main__.$func_2 | 1 | 1 | 0 | 2 |
| __main__.$func_3 | 1 | 1 | 0 | 2 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.get_filesystem_encoding | 1 | 1 | 0 | 0 |
| __main__.get_best_encoding | 1 | 1 | 0 | 0 |
| __main__._find_binary_reader | 1 | 1 | 0 | 0 |
| __main__.get_text_stderr | 1 | 1 | 0 | 0 |
| __main__.open_stream | 1 | 1 | 0 | 0 |
| __main__.strip_ansi | 1 | 1 | 0 | 0 |
| __main__.term_len | 1 | 1 | 0 | 0 |
| __main__.isatty | 1 | 1 | 0 | 0 |
| __main__.$func_2 | 1 | 1 | 0 | 0 |
| __main__.$func_3 | 1 | 1 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| get_template_locals | 22 | 1 | 1 | 16 |
| next | 17 | 1 | 1 | 27 |
| compile_rules | 15 | 1 | 1 | 31 |
| _unpack_args | 12 | 1 | 1 | 19 |
| measure_table | 11 | 1 | 1 | 13 |
| style | 10 | 1 | 1 | 19 |
| convert_type | 8 | 1 | 1 | 12 |
| find_undeclared | 8 | 1 | 1 | 12 |
| find_best_app | 8 | 1 | 1 | 15 |
| getattr | 6 | 1 | 1 | 16 |

## Memory Usage

- **Peak memory**: 118.83 MB
- **Current memory**: 111.59 MB

## Error Analysis

| Error Type | Count | Affected Modules |
|-----------|-------|------------------|
| AssertionError | 4 | site-packages/jinja2/async_utils.py, site-packages/jinja2/environment.py, site-packages/jinja2/filters.py (+1 more) |

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| site-packages/click/__init__.py | 0.01 | 0 | 72 |
| site-packages/click/_compat.py | 0.62 | 51 | 460 |
| site-packages/click/_termui_impl.py | 1.04 | 33 | 533 |
| site-packages/click/_textwrap.py | 0.10 | 3 | 39 |
| site-packages/click/_winconsole.py | 0.14 | 20 | 215 |
| site-packages/click/core.py | 3.04 | 131 | 2272 |
| site-packages/click/decorators.py | 0.53 | 18 | 376 |
| site-packages/click/exceptions.py | 0.29 | 18 | 219 |
| site-packages/click/formatting.py | 0.37 | 16 | 246 |
| site-packages/click/globals.py | 0.09 | 6 | 47 |
| site-packages/click/parser.py | 0.63 | 20 | 369 |
| site-packages/click/shell_completion.py | 0.34 | 26 | 438 |
| site-packages/click/termui.py | 0.48 | 17 | 635 |
| site-packages/click/testing.py | 0.49 | 25 | 386 |
| site-packages/click/types.py | 1.16 | 61 | 796 |
| site-packages/click/utils.py | 0.39 | 30 | 436 |
| site-packages/jinja2/__init__.py | 0.02 | 0 | 36 |
| site-packages/jinja2/_identifier.py | 0.01 | 0 | 4 |
| site-packages/jinja2/bccache.py | 0.36 | 24 | 309 |
| site-packages/jinja2/compiler.py | 3.65 | 111 | 1533 |

*...and 41 more modules*

## Failed Modules

| Module | Error Type | Error Message |
|--------|-----------|---------------|
| site-packages/jinja2/async_utils.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/environment.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/filters.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/runtime.py | AssertionError | The test variable of IfEdge should be ast.Name! |