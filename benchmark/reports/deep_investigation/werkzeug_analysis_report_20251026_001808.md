# WERKZEUG - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T00:17:44.294661

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 23.66 seconds
- **Modules analyzed**: 44
- **Modules succeeded**: 44
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 733.1 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 1989
- **Non-empty points-to sets**: 1989
- **Singleton sets**: 1637 (82.3%)
- **Empty sets**: 0
- **Average set size**: 1.32
- **Maximum set size**: 36
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 1637 |
| 11-20 | 2 |
| 2-5 | 332 |
| 21-50 | 1 |
| 6-10 | 17 |

## Call Graph Metrics

- **Total functions**: 756
- **Total call edges**: 160
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 190
- **Classes with MRO**: 190
- **Maximum MRO length**: 3
- **Average MRO length**: 2.25

## Object Metrics

- **Total objects created**: 1283
- **Average objects per variable**: 1.32
- **Variables with no objects**: 0
- **Variables with singleton**: 1637
- **Variables with multiple objects**: 352

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 1052 |
| class | 170 |
| func | 61 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.406
- **Functions with no calls**: 530
- **Functions with no objects**: 554
- **Average objects per function**: 0.5
- **Average calls per function**: 0.2
- **Max objects in a function**: 72
- **Max calls in a function**: 2

**Interpretation**: moderate correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 589

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 530 (90.0%) |
| 1 call | 59 (10.0%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 554 (94.1%) |
| 1-10 objects | 26 (4.4%) |
| 11-50 objects | 8 (1.4%) |
| 51-100 objects | 1 (0.2%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| unescape | 1 | 1 | 5 | 3 |
| escape | 1 | 1 | 0 | 5 |
| _has_level_handler | 1 | 1 | 4 | 2 |
| _iter_module_paths | 1 | 1 | 9 | 5 |
| _remove_by_pattern | 1 | 1 | 3 | 5 |
| _find_watchdog_paths | 1 | 1 | 6 | 2 |
| _find_common_roots | 1 | 1 | 18 | 4 |
| _get_args_for_reloading | 1 | 1 | 1 | 2 |
| ensure_echo_on | 1 | 1 | 0 | 2 |
| is_immutable | 1 | 1 | 13 | 14 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| unescape | 1 | 1 | 5 | 14 |
| escape | 1 | 1 | 0 | 0 |
| _has_level_handler | 1 | 1 | 4 | 2 |
| _iter_module_paths | 1 | 1 | 9 | 11 |
| _remove_by_pattern | 1 | 1 | 3 | 4 |
| _find_watchdog_paths | 1 | 1 | 6 | 7 |
| _find_common_roots | 1 | 1 | 18 | 21 |
| _get_args_for_reloading | 1 | 1 | 1 | 2 |
| ensure_echo_on | 1 | 1 | 0 | 0 |
| is_immutable | 1 | 1 | 13 | 13 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| _process_traceback | 72 | 1 | 1 | 66 |
| _unquote_to_bytes | 30 | 1 | 1 | 72 |
| url_unparse | 27 | 1 | 1 | 15 |
| url_quote | 20 | 1 | 1 | 96 |
| _find_common_roots | 18 | 1 | 1 | 21 |
| url_parse | 15 | 1 | 1 | 21 |
| stream_encode_multipart | 14 | 1 | 1 | 29 |
| is_immutable | 13 | 1 | 1 | 13 |
| repr | 12 | 1 | 1 | 15 |
| generate_adhoc_ssl_pair | 10 | 1 | 1 | 14 |

## Memory Usage

- **Peak memory**: 98.15 MB
- **Current memory**: 89.58 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| site-packages/markupsafe/__init__.py | 0.60 | 29 | 213 |
| site-packages/markupsafe/_native.py | 0.09 | 3 | 49 |
| __init__.py | 0.01 | 0 | 5 |
| _internal.py | 0.59 | 37 | 417 |
| _reloader.py | 0.93 | 24 | 312 |
| datastructures.py | 3.16 | 302 | 2371 |
| debug/__init__.py | 0.58 | 18 | 410 |
| debug/console.py | 0.30 | 28 | 165 |
| debug/repr.py | 0.52 | 17 | 244 |
| debug/tbtools.py | 0.70 | 15 | 355 |
| exceptions.py | 0.51 | 25 | 652 |
| formparser.py | 0.45 | 17 | 383 |
| http.py | 0.84 | 40 | 1063 |
| local.py | 0.45 | 37 | 476 |
| middleware/__init__.py | 0.01 | 0 | 18 |
| middleware/dispatcher.py | 0.09 | 2 | 62 |
| middleware/http_proxy.py | 0.39 | 3 | 186 |
| middleware/lint.py | 0.61 | 24 | 340 |
| middleware/profiler.py | 0.15 | 2 | 113 |
| middleware/proxy_fix.py | 0.15 | 3 | 151 |

*...and 24 more modules*