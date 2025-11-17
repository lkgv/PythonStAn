# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-25T22:02:37.170162

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 0.33 seconds
- **Modules analyzed**: 3
- **Modules succeeded**: 3
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 5757.3 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 77
- **Non-empty points-to sets**: 77
- **Singleton sets**: 68 (88.3%)
- **Empty sets**: 0
- **Average set size**: 1.16
- **Maximum set size**: 4
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 68 |
| 2-5 | 9 |

## Call Graph Metrics

- **Total functions**: 72
- **Total call edges**: 2
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 1
- **Classes with MRO**: 1
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.01 | 1 | 63 |
| __main__.py | 0.00 | 0 | 2 |
| app.py | 0.32 | 78 | 1857 |