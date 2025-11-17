# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T00:39:31.031676

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 5.55 seconds
- **Modules analyzed**: 15
- **Modules succeeded**: 15
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 1077.6 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 327
- **Non-empty points-to sets**: 327
- **Singleton sets**: 272 (83.2%)
- **Empty sets**: 0
- **Average set size**: 1.29
- **Maximum set size**: 13
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 272 |
| 11-20 | 1 |
| 2-5 | 54 |

## Call Graph Metrics

- **Total functions**: 4584
- **Total call edges**: 31
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 35
- **Classes with MRO**: 35
- **Maximum MRO length**: 2
- **Average MRO length**: 2.00

## Object Metrics

- **Total objects created**: 236
- **Average objects per variable**: 1.29
- **Variables with no objects**: 0
- **Variables with singleton**: 272
- **Variables with multiple objects**: 55

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 181 |
| class | 35 |
| func | 20 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.476
- **Functions with no calls**: 478
- **Functions with no objects**: 494
- **Average objects per function**: 0.0
- **Average calls per function**: 0.1
- **Max objects in a function**: 5
- **Max calls in a function**: 2

**Interpretation**: moderate correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 501

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 478 (95.4%) |
| 1 call | 23 (4.6%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 494 (98.6%) |
| 1-10 objects | 7 (1.4%) |
| 11-50 objects | 0 (0.0%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__.get_debug_flag | 1 | 1 | 0 | 2 |
| __main__.get_load_dotenv | 1 | 1 | 0 | 2 |
| __main__.get_root_path | 1 | 1 | 0 | 2 |
| __main__._split_blueprint_path | 1 | 1 | 0 | 2 |
| __main__._default | 1 | 1 | 0 | 2 |
| __main__.JSONProvider.response | 1 | 1 | 1 | 2 |
| __main__.create_logger | 1 | 1 | 0 | 2 |
| __main__._endpoint_from_view_func | 1 | 1 | 0 | 2 |
| __main__.find_package | 1 | 1 | 0 | 2 |
| iscoroutinefunction | 1 | 1 | 0 | 2 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.get_debug_flag | 1 | 1 | 0 | 0 |
| __main__.get_load_dotenv | 1 | 1 | 0 | 0 |
| __main__.get_root_path | 1 | 1 | 0 | 0 |
| __main__._split_blueprint_path | 1 | 1 | 0 | 0 |
| __main__._default | 1 | 1 | 0 | 0 |
| __main__.JSONProvider.response | 1 | 1 | 1 | 1 |
| __main__.create_logger | 1 | 1 | 0 | 0 |
| __main__._endpoint_from_view_func | 1 | 1 | 0 | 0 |
| __main__.find_package | 1 | 1 | 0 | 0 |
| iscoroutinefunction | 1 | 1 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| find_best_app | 5 | 1 | 1 | 8 |
| dumps | 5 | 1 | 1 | 10 |
| loads | 3 | 1 | 1 | 8 |
| _find_package_path | 3 | 1 | 1 | 2 |
| _split_blueprint_path | 2 | 1 | 1 | 4 |
| __main__.JSONProvider.response | 1 | 1 | 1 | 1 |
| has_level_handler | 1 | 1 | 1 | 1 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.iscoroutinefunction | 0 | 0 | 0 | 0 |
| __main__._make_timedelta | 0 | 0 | 0 | 0 |

## Memory Usage

- **Peak memory**: 33.75 MB
- **Current memory**: 24.85 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.03 | 1 | 63 |
| __main__.py | 0.00 | 0 | 2 |
| app.py | 1.54 | 78 | 1857 |
| blueprints.py | 0.62 | 28 | 578 |
| cli.py | 1.07 | 31 | 760 |
| config.py | 0.31 | 12 | 262 |
| ctx.py | 0.26 | 26 | 324 |
| debughelpers.py | 0.22 | 6 | 129 |
| globals.py | 0.08 | 6 | 87 |
| helpers.py | 0.32 | 21 | 532 |
| json/__init__.py | 0.16 | 10 | 259 |
| json/provider.py | 0.27 | 11 | 240 |
| json/tag.py | 0.24 | 33 | 219 |
| logging.py | 0.07 | 3 | 50 |
| scaffold.py | 0.36 | 36 | 620 |