# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-17T22:52:11.376306

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 0.66 seconds
- **Modules analyzed**: 5
- **Modules succeeded**: 5
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 4933.0 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 38
- **Non-empty points-to sets**: 38
- **Singleton sets**: 34 (89.5%)
- **Empty sets**: 0
- **Average set size**: 1.11
- **Maximum set size**: 2
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 34 |
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
| cli.py | 0.21 | 18 | 760 |