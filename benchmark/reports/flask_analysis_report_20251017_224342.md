# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-17T22:43:41.540782

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 1.34 seconds
- **Modules analyzed**: 22
- **Modules succeeded**: 22
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 5234.1 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 62
- **Non-empty points-to sets**: 62
- **Singleton sets**: 52 (83.9%)
- **Empty sets**: 0
- **Average set size**: 1.16
- **Maximum set size**: 2
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 52 |
| 2-5 | 10 |

## Call Graph Metrics

- **Total functions**: 80
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
| config.py | 0.05 | 0 | 262 |
| ctx.py | 0.05 | 4 | 324 |
| debughelpers.py | 0.04 | 3 | 129 |
| globals.py | 0.02 | 1 | 87 |
| helpers.py | 0.07 | 17 | 532 |
| json/__init__.py | 0.04 | 7 | 259 |
| json/provider.py | 0.06 | 1 | 240 |
| json/tag.py | 0.05 | 0 | 219 |
| logging.py | 0.01 | 3 | 50 |
| scaffold.py | 0.08 | 6 | 620 |
| sessions.py | 0.05 | 0 | 294 |
| signals.py | 0.01 | 0 | 40 |
| templating.py | 0.04 | 7 | 166 |
| testing.py | 0.05 | 0 | 235 |
| typing.py | 0.01 | 0 | 56 |

*...and 2 more modules*