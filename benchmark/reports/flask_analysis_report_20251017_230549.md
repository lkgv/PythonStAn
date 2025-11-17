# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-17T23:05:48.269402

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 1.31 seconds
- **Modules analyzed**: 22
- **Modules succeeded**: 22
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 5326.7 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 297
- **Non-empty points-to sets**: 297
- **Singleton sets**: 258 (86.9%)
- **Empty sets**: 0
- **Average set size**: 1.15
- **Maximum set size**: 4
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 258 |
| 2-5 | 39 |

## Call Graph Metrics

- **Total functions**: 305
- **Total call edges**: 0
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 51
- **Classes with MRO**: 51
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.01 | 1 | 63 |
| __main__.py | 0.00 | 0 | 2 |
| app.py | 0.32 | 78 | 1857 |
| blueprints.py | 0.13 | 28 | 578 |
| cli.py | 0.21 | 31 | 760 |
| config.py | 0.05 | 12 | 262 |
| ctx.py | 0.05 | 26 | 324 |
| debughelpers.py | 0.04 | 6 | 129 |
| globals.py | 0.02 | 6 | 87 |
| helpers.py | 0.06 | 21 | 532 |
| json/__init__.py | 0.05 | 10 | 259 |
| json/provider.py | 0.06 | 11 | 240 |
| json/tag.py | 0.05 | 33 | 219 |
| logging.py | 0.01 | 3 | 50 |
| scaffold.py | 0.07 | 36 | 620 |
| sessions.py | 0.05 | 22 | 294 |
| signals.py | 0.01 | 4 | 40 |
| templating.py | 0.04 | 14 | 166 |
| testing.py | 0.04 | 11 | 235 |
| typing.py | 0.01 | 0 | 56 |

*...and 2 more modules*