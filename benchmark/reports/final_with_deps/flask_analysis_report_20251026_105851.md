# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T10:58:14.203988

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 30.56 seconds
- **Modules analyzed**: 50
- **Modules succeeded**: 45
- **Modules failed**: 5
- **Success rate**: 90.0%
- **Throughput**: 606.9 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 3007
- **Non-empty points-to sets**: 3007
- **Singleton sets**: 2415 (80.3%)
- **Empty sets**: 0
- **Average set size**: 1.39
- **Maximum set size**: 33
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 2415 |
| 11-20 | 21 |
| 2-5 | 539 |
| 21-50 | 1 |
| 6-10 | 31 |

## Call Graph Metrics

- **Total functions**: 1883
- **Total call edges**: 185
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 224
- **Classes with MRO**: 224
- **Maximum MRO length**: 2
- **Average MRO length**: 2.00

## Object Metrics

- **Total objects created**: 2971
- **Average objects per variable**: 1.39
- **Variables with no objects**: 0
- **Variables with singleton**: 2415
- **Variables with multiple objects**: 592

### Object Type Distribution

| Type | Count |
|------|-------|
| class | 1463 |
| alloc | 1440 |
| func | 68 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.559
- **Functions with no calls**: 1790
- **Functions with no objects**: 1823
- **Average objects per function**: 0.1
- **Average calls per function**: 0.1
- **Max objects in a function**: 22
- **Max calls in a function**: 2

**Interpretation**: strong correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 1883

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 1790 (95.1%) |
| 1 call | 93 (4.9%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 1823 (96.8%) |
| 1-10 objects | 55 (2.9%) |
| 11-50 objects | 5 (0.3%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__.get_filesystem_encoding | 1 | 1 | 0 | 2 |
| __main__.is_ascii_encoding | 1 | 1 | 0 | 4 |
| __main__.get_best_encoding | 1 | 1 | 2 | 3 |
| __main__._is_binary_reader | 1 | 1 | 1 | 3 |
| __main__._is_binary_writer | 1 | 1 | 1 | 2 |
| __main__._find_binary_reader | 1 | 1 | 0 | 4 |
| __main__._find_binary_writer | 1 | 1 | 0 | 2 |
| __main__.get_binary_stdout | 1 | 1 | 0 | 2 |
| __main__.get_text_stderr | 1 | 1 | 0 | 2 |
| __main__.open_stream | 1 | 1 | 3 | 2 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.get_filesystem_encoding | 1 | 1 | 0 | 0 |
| __main__.is_ascii_encoding | 1 | 1 | 0 | 0 |
| __main__.get_best_encoding | 1 | 1 | 2 | 6 |
| __main__._is_binary_reader | 1 | 1 | 1 | 3 |
| __main__._is_binary_writer | 1 | 1 | 1 | 1 |
| __main__._find_binary_reader | 1 | 1 | 0 | 0 |
| __main__._find_binary_writer | 1 | 1 | 0 | 0 |
| __main__.get_binary_stdout | 1 | 1 | 0 | 0 |
| __main__.get_text_stderr | 1 | 1 | 0 | 0 |
| __main__.open_stream | 1 | 1 | 3 | 5 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| get_template_locals | 22 | 1 | 1 | 16 |
| compile_rules | 15 | 1 | 1 | 34 |
| measure_table | 13 | 1 | 1 | 15 |
| _unpack_args | 13 | 1 | 1 | 19 |
| style | 12 | 1 | 1 | 22 |
| __main__.convert_type | 10 | 1 | 1 | 18 |
| __main__.echo | 10 | 1 | 1 | 20 |
| convert_type | 10 | 1 | 1 | 18 |
| _build_prompt | 9 | 1 | 1 | 18 |
| find_best_app | 9 | 1 | 1 | 19 |

## Memory Usage

- **Peak memory**: 132.38 MB
- **Current memory**: 117.74 MB

## Error Analysis

| Error Type | Count | Affected Modules |
|-----------|-------|------------------|
| AssertionError | 4 | site-packages/jinja2/async_utils.py, site-packages/jinja2/environment.py, site-packages/jinja2/filters.py (+1 more) |
| TypeError | 1 | site-packages/jinja2/loaders.py |

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| site-packages/click/__init__.py | 0.01 | 0 | 72 |
| site-packages/click/_compat.py | 0.59 | 51 | 460 |
| site-packages/click/_termui_impl.py | 1.18 | 33 | 533 |
| site-packages/click/_textwrap.py | 0.08 | 3 | 39 |
| site-packages/click/_winconsole.py | 0.13 | 20 | 215 |
| site-packages/click/core.py | 3.13 | 131 | 2272 |
| site-packages/click/decorators.py | 0.52 | 18 | 376 |
| site-packages/click/exceptions.py | 0.30 | 18 | 219 |
| site-packages/click/formatting.py | 0.37 | 16 | 246 |
| site-packages/click/globals.py | 0.05 | 6 | 47 |
| site-packages/click/parser.py | 0.59 | 20 | 369 |
| site-packages/click/shell_completion.py | 0.32 | 26 | 438 |
| site-packages/click/termui.py | 0.70 | 17 | 635 |
| site-packages/click/testing.py | 0.49 | 25 | 386 |
| site-packages/click/types.py | 1.22 | 61 | 796 |
| site-packages/click/utils.py | 0.36 | 30 | 436 |
| site-packages/jinja2/__init__.py | 0.01 | 0 | 36 |
| site-packages/jinja2/_identifier.py | 0.01 | 0 | 4 |
| site-packages/jinja2/bccache.py | 0.33 | 24 | 309 |
| site-packages/jinja2/compiler.py | 4.65 | 111 | 1533 |

*...and 25 more modules*

## Failed Modules

| Module | Error Type | Error Message |
|--------|-----------|---------------|
| site-packages/jinja2/async_utils.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/environment.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/filters.py | AssertionError | The test variable of IfEdge should be ast.Name! |
| site-packages/jinja2/loaders.py | TypeError | 'frozenset' object is not subscriptable |
| site-packages/jinja2/runtime.py | AssertionError | The test variable of IfEdge should be ast.Name! |