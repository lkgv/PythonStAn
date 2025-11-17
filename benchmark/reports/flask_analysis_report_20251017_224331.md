# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-17T22:43:30.754004

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 0.67 seconds
- **Modules analyzed**: 5
- **Modules succeeded**: 5
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 4843.1 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 29
- **Non-empty points-to sets**: 29
- **Singleton sets**: 25 (86.2%)
- **Empty sets**: 0
- **Average set size**: 1.14
- **Maximum set size**: 2
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 25 |
| 2-5 | 4 |

## Call Graph Metrics

- **Total functions**: 23
- **Total call edges**: 0
- **Average out-degree**: 0.00

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.01 | 1 | 63 |
| __main__.py | 0.00 | 0 | 2 |
| app.py | 0.31 | 2 | 1857 |
| blueprints.py | 0.13 | 0 | 578 |
| cli.py | 0.22 | 18 | 760 |