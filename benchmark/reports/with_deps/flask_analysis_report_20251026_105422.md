# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T10:54:09.951845

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 10.54 seconds
- **Modules analyzed**: 20
- **Modules succeeded**: 19
- **Modules failed**: 1
- **Success rate**: 95.0%
- **Throughput**: 748.7 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 1111
- **Non-empty points-to sets**: 1111
- **Singleton sets**: 901 (81.1%)
- **Empty sets**: 0
- **Average set size**: 1.29
- **Maximum set size**: 20
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 901 |
| 11-20 | 2 |
| 2-5 | 204 |
| 6-10 | 4 |

## Call Graph Metrics

- **Total functions**: 757
- **Total call edges**: 129
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 72
- **Classes with MRO**: 72
- **Maximum MRO length**: 3
- **Average MRO length**: 2.60

## Object Metrics

- **Total objects created**: 1111
- **Average objects per variable**: 1.29
- **Variables with no objects**: 0
- **Variables with singleton**: 901
- **Variables with multiple objects**: 210

### Object Type Distribution

| Type | Count |
|------|-------|
| class | 557 |
| alloc | 522 |
| func | 32 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.567
- **Functions with no calls**: 691
- **Functions with no objects**: 715
- **Average objects per function**: 0.2
- **Average calls per function**: 0.2
- **Max objects in a function**: 13
- **Max calls in a function**: 2

**Interpretation**: strong correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 757

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 691 (91.3%) |
| 1 call | 66 (8.7%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 715 (94.5%) |
| 1-10 objects | 39 (5.2%) |
| 11-50 objects | 3 (0.4%) |
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
| measure_table | 13 | 1 | 1 | 15 |
| _unpack_args | 13 | 1 | 1 | 19 |
| style | 12 | 1 | 1 | 22 |
| __main__.convert_type | 10 | 1 | 1 | 18 |
| __main__.echo | 10 | 1 | 1 | 20 |
| convert_type | 10 | 1 | 1 | 18 |
| _build_prompt | 9 | 1 | 1 | 18 |
| _format_default | 8 | 1 | 1 | 24 |
| __main__.split_arg_string | 5 | 1 | 1 | 9 |
| __main__._expand_args | 4 | 1 | 1 | 6 |

## Memory Usage

- **Peak memory**: 55.13 MB
- **Current memory**: 52.11 MB

## Error Analysis

| Error Type | Count | Affected Modules |
|-----------|-------|------------------|
| AssertionError | 1 | site-packages/jinja2/async_utils.py |

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| site-packages/click/__init__.py | 0.01 | 0 | 72 |
| site-packages/click/_compat.py | 0.73 | 51 | 460 |
| site-packages/click/_termui_impl.py | 1.08 | 33 | 533 |
| site-packages/click/_textwrap.py | 0.08 | 3 | 39 |
| site-packages/click/_winconsole.py | 0.19 | 20 | 215 |
| site-packages/click/core.py | 3.24 | 131 | 2272 |
| site-packages/click/decorators.py | 0.44 | 18 | 376 |
| site-packages/click/exceptions.py | 0.30 | 18 | 219 |
| site-packages/click/formatting.py | 0.40 | 16 | 246 |
| site-packages/click/globals.py | 0.15 | 6 | 47 |
| site-packages/click/parser.py | 0.60 | 20 | 369 |
| site-packages/click/shell_completion.py | 0.34 | 26 | 438 |
| site-packages/click/termui.py | 0.62 | 17 | 635 |
| site-packages/click/testing.py | 0.48 | 25 | 386 |
| site-packages/click/types.py | 1.07 | 61 | 796 |
| site-packages/click/utils.py | 0.47 | 30 | 436 |
| site-packages/jinja2/__init__.py | 0.01 | 0 | 36 |
| site-packages/jinja2/_identifier.py | 0.00 | 0 | 4 |
| site-packages/jinja2/bccache.py | 0.31 | 24 | 309 |

## Failed Modules

| Module | Error Type | Error Message |
|--------|-----------|---------------|
| site-packages/jinja2/async_utils.py | AssertionError | The test variable of IfEdge should be ast.Name! |