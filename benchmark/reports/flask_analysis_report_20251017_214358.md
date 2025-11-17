# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-17T21:38:53.539916

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 304.68 seconds
- **Modules analyzed**: 10
- **Modules succeeded**: 10
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 15.1 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 47
- **Non-empty points-to sets**: 47
- **Singleton sets**: 39 (83.0%)
- **Empty sets**: 0
- **Average set size**: 1.17
- **Maximum set size**: 2
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 39 |
| 2-5 | 8 |

## Call Graph Metrics

- **Total functions**: 49
- **Total call edges**: 0
- **Average out-degree**: 0.00

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 39.00 | 1 | 63 |
| __main__.py | 0.46 | 0 | 2 |
| app.py | 48.95 | 2 | 1857 |
| blueprints.py | 42.12 | 0 | 578 |
| cli.py | 43.85 | 18 | 760 |
| config.py | 43.08 | 0 | 262 |
| ctx.py | 0.56 | 4 | 324 |
| debughelpers.py | 0.06 | 3 | 129 |
| globals.py | 41.93 | 1 | 87 |
| helpers.py | 44.66 | 17 | 532 |