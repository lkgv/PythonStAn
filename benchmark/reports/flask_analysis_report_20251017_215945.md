# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-17T21:56:41.228967

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 184.11 seconds
- **Modules analyzed**: 5
- **Modules succeeded**: 5
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 17.7 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 22
- **Non-empty points-to sets**: 22
- **Singleton sets**: 18 (81.8%)
- **Empty sets**: 0
- **Average set size**: 1.18
- **Maximum set size**: 2
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 18 |
| 2-5 | 4 |

## Call Graph Metrics

- **Total functions**: 23
- **Total call edges**: 0
- **Average out-degree**: 0.00

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 42.34 | 1 | 63 |
| __main__.py | 0.49 | 0 | 2 |
| app.py | 52.70 | 2 | 1857 |
| blueprints.py | 44.20 | 0 | 578 |
| cli.py | 44.38 | 18 | 760 |