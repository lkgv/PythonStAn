# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-17T21:44:40.023752

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 481.26 seconds
- **Modules analyzed**: 22
- **Modules succeeded**: 22
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 14.5 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 54
- **Non-empty points-to sets**: 54
- **Singleton sets**: 45 (83.3%)
- **Empty sets**: 0
- **Average set size**: 1.17
- **Maximum set size**: 2
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 45 |
| 2-5 | 9 |

## Call Graph Metrics

- **Total functions**: 80
- **Total call edges**: 0
- **Average out-degree**: 0.00

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 39.49 | 1 | 63 |
| __main__.py | 0.45 | 0 | 2 |
| app.py | 47.62 | 2 | 1857 |
| blueprints.py | 42.52 | 0 | 578 |
| cli.py | 44.92 | 18 | 760 |
| config.py | 43.80 | 0 | 262 |
| ctx.py | 0.57 | 4 | 324 |
| debughelpers.py | 0.06 | 3 | 129 |
| globals.py | 41.48 | 1 | 87 |
| helpers.py | 44.10 | 17 | 532 |
| json/__init__.py | 42.28 | 7 | 259 |
| json/provider.py | 44.28 | 1 | 240 |
| json/tag.py | 0.60 | 0 | 219 |
| logging.py | 0.04 | 3 | 50 |
| scaffold.py | 45.30 | 6 | 620 |
| sessions.py | 43.03 | 0 | 294 |
| signals.py | 0.54 | 0 | 40 |
| templating.py | 0.05 | 7 | 166 |
| testing.py | 0.06 | 0 | 235 |
| typing.py | 0.03 | 0 | 56 |

*...and 2 more modules*