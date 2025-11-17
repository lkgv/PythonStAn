# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-17T23:02:54.204026

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 0.31 seconds
- **Modules analyzed**: 3
- **Modules succeeded**: 3
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 6122.3 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 1
- **Non-empty points-to sets**: 1
- **Singleton sets**: 1 (100.0%)
- **Empty sets**: 0
- **Average set size**: 1.00
- **Maximum set size**: 1
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 1 |

## Call Graph Metrics

- **Total functions**: 4
- **Total call edges**: 0
- **Average out-degree**: 0.00

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.01 | 1 | 63 |
| __main__.py | 0.00 | 0 | 2 |
| app.py | 0.31 | 2 | 1857 |