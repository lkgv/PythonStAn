# WERKZEUG - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-17T21:52:55.647103

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 423.44 seconds
- **Modules analyzed**: 10
- **Modules succeeded**: 10
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 12.5 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 102
- **Non-empty points-to sets**: 102
- **Singleton sets**: 81 (79.4%)
- **Empty sets**: 0
- **Average set size**: 1.22
- **Maximum set size**: 3
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 81 |
| 2-5 | 21 |

## Call Graph Metrics

- **Total functions**: 56
- **Total call edges**: 0
- **Average out-degree**: 0.00

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.00 | 0 | 5 |
| _internal.py | 46.68 | 26 | 417 |
| _reloader.py | 45.28 | 8 | 312 |
| datastructures.py | 45.67 | 12 | 2371 |
| debug/__init__.py | 46.45 | 3 | 410 |
| debug/console.py | 46.35 | 0 | 165 |
| debug/repr.py | 51.05 | 4 | 244 |
| debug/tbtools.py | 49.34 | 2 | 355 |
| exceptions.py | 46.51 | 2 | 652 |
| formparser.py | 46.10 | 5 | 383 |