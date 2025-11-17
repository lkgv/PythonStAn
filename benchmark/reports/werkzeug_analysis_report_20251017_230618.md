# WERKZEUG - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-17T23:06:14.005861

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 4.82 seconds
- **Modules analyzed**: 42
- **Modules succeeded**: 42
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 3543.4 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 1143
- **Non-empty points-to sets**: 1143
- **Singleton sets**: 994 (87.0%)
- **Empty sets**: 0
- **Average set size**: 1.14
- **Maximum set size**: 6
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 994 |
| 2-5 | 148 |
| 6-10 | 1 |

## Call Graph Metrics

- **Total functions**: 727
- **Total call edges**: 0
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 186
- **Classes with MRO**: 186
- **Maximum MRO length**: 3
- **Average MRO length**: 2.25

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.00 | 0 | 5 |
| _internal.py | 0.13 | 37 | 417 |
| _reloader.py | 0.17 | 24 | 312 |
| datastructures.py | 0.61 | 302 | 2371 |
| debug/__init__.py | 0.14 | 18 | 410 |
| debug/console.py | 0.06 | 28 | 165 |
| debug/repr.py | 0.10 | 17 | 244 |
| debug/tbtools.py | 0.12 | 15 | 355 |
| exceptions.py | 0.09 | 25 | 652 |
| formparser.py | 0.09 | 17 | 383 |
| http.py | 0.22 | 40 | 1063 |
| local.py | 0.10 | 37 | 476 |
| middleware/__init__.py | 0.00 | 0 | 18 |
| middleware/dispatcher.py | 0.02 | 2 | 62 |
| middleware/http_proxy.py | 0.08 | 3 | 186 |
| middleware/lint.py | 0.10 | 24 | 340 |
| middleware/profiler.py | 0.03 | 2 | 113 |
| middleware/proxy_fix.py | 0.03 | 3 | 151 |
| middleware/shared_data.py | 0.14 | 8 | 221 |
| routing/__init__.py | 0.00 | 0 | 102 |

*...and 22 more modules*