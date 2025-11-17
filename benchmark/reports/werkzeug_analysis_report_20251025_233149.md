# WERKZEUG - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-25T23:31:24.830845

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 24.87 seconds
- **Modules analyzed**: 44
- **Modules succeeded**: 44
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 697.3 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 1990
- **Non-empty points-to sets**: 1990
- **Singleton sets**: 1640 (82.4%)
- **Empty sets**: 0
- **Average set size**: 1.31
- **Maximum set size**: 36
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 1640 |
| 11-20 | 2 |
| 2-5 | 330 |
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
- **Average objects per variable**: 1.31
- **Variables with no objects**: 0
- **Variables with singleton**: 1640
- **Variables with multiple objects**: 350

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 1052 |
| class | 170 |
| func | 61 |

## Function Metrics

- **Total functions tracked**: 59

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Contexts |
|----------|------------|-----------|----------|
| escape | 1 | 1 | 5 |
| unescape | 1 | 1 | 3 |
| _has_level_handler | 1 | 1 | 2 |
| _get_args_for_reloading | 1 | 1 | 2 |
| _find_watchdog_paths | 1 | 1 | 2 |
| _iter_module_paths | 1 | 1 | 5 |
| _find_common_roots | 1 | 1 | 4 |
| ensure_echo_on | 1 | 1 | 2 |
| _remove_by_pattern | 1 | 1 | 5 |
| is_immutable | 1 | 1 | 14 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Variables |
|----------|-----------|------------|-----------|
| escape | 1 | 1 | 0 |
| unescape | 1 | 1 | 14 |
| _has_level_handler | 1 | 1 | 2 |
| _get_args_for_reloading | 1 | 1 | 2 |
| _find_watchdog_paths | 1 | 1 | 7 |
| _iter_module_paths | 1 | 1 | 3 |
| _find_common_roots | 1 | 1 | 21 |
| ensure_echo_on | 1 | 1 | 0 |
| _remove_by_pattern | 1 | 1 | 13 |
| is_immutable | 1 | 1 | 13 |

## Memory Usage

- **Peak memory**: 98.15 MB
- **Current memory**: 89.58 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| site-packages/markupsafe/__init__.py | 0.65 | 29 | 213 |
| site-packages/markupsafe/_native.py | 0.12 | 3 | 49 |
| __init__.py | 0.01 | 0 | 5 |
| _internal.py | 0.66 | 37 | 417 |
| _reloader.py | 1.03 | 24 | 312 |
| datastructures.py | 3.48 | 302 | 2371 |
| debug/__init__.py | 0.70 | 18 | 410 |
| debug/console.py | 0.33 | 28 | 165 |
| debug/repr.py | 0.57 | 17 | 244 |
| debug/tbtools.py | 0.83 | 15 | 355 |
| exceptions.py | 0.56 | 25 | 652 |
| formparser.py | 0.53 | 17 | 383 |
| http.py | 1.01 | 40 | 1063 |
| local.py | 0.51 | 37 | 476 |
| middleware/__init__.py | 0.01 | 0 | 18 |
| middleware/dispatcher.py | 0.08 | 2 | 62 |
| middleware/http_proxy.py | 0.41 | 3 | 186 |
| middleware/lint.py | 0.63 | 24 | 340 |
| middleware/profiler.py | 0.14 | 2 | 113 |
| middleware/proxy_fix.py | 0.15 | 3 | 151 |

*...and 24 more modules*