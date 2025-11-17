# WERKZEUG - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-17T22:44:01.572289

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 4.49 seconds
- **Modules analyzed**: 42
- **Modules succeeded**: 42
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 3806.9 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 326
- **Non-empty points-to sets**: 326
- **Singleton sets**: 275 (84.4%)
- **Empty sets**: 0
- **Average set size**: 1.16
- **Maximum set size**: 3
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 275 |
| 2-5 | 51 |

## Call Graph Metrics

- **Total functions**: 196
- **Total call edges**: 0
- **Average out-degree**: 0.00

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.00 | 0 | 5 |
| _internal.py | 0.12 | 26 | 417 |
| _reloader.py | 0.17 | 8 | 312 |
| datastructures.py | 0.61 | 12 | 2371 |
| debug/__init__.py | 0.12 | 3 | 410 |
| debug/console.py | 0.06 | 0 | 165 |
| debug/repr.py | 0.10 | 4 | 244 |
| debug/tbtools.py | 0.12 | 2 | 355 |
| exceptions.py | 0.09 | 2 | 652 |
| formparser.py | 0.09 | 5 | 383 |
| http.py | 0.19 | 40 | 1063 |
| local.py | 0.09 | 3 | 476 |
| middleware/__init__.py | 0.00 | 0 | 18 |
| middleware/dispatcher.py | 0.02 | 0 | 62 |
| middleware/http_proxy.py | 0.08 | 0 | 186 |
| middleware/lint.py | 0.10 | 1 | 340 |
| middleware/profiler.py | 0.03 | 0 | 113 |
| middleware/proxy_fix.py | 0.03 | 0 | 151 |
| middleware/shared_data.py | 0.10 | 0 | 221 |
| routing/__init__.py | 0.00 | 0 | 102 |

*...and 22 more modules*