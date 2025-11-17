# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T01:17:17.003088

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 23.95 seconds
- **Modules analyzed**: 10
- **Modules succeeded**: 10
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 191.8 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 5029
- **Non-empty points-to sets**: 5029
- **Singleton sets**: 2380 (47.3%)
- **Empty sets**: 0
- **Average set size**: 4.79
- **Maximum set size**: 108
- **Median set size**: 2.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 2380 |
| 11-20 | 520 |
| 2-5 | 1229 |
| 21-50 | 120 |
| 51+ | 10 |
| 6-10 | 770 |

## Call Graph Metrics

- **Total functions**: 2166
- **Total call edges**: 170
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 29
- **Classes with MRO**: 29
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 277
- **Average objects per variable**: 4.79
- **Variables with no objects**: 0
- **Variables with singleton**: 2380
- **Variables with multiple objects**: 2649

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 242 |
| class | 20 |
| func | 15 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.495
- **Functions with no calls**: 342
- **Functions with no objects**: 352
- **Average objects per function**: 0.1
- **Average calls per function**: 0.1
- **Max objects in a function**: 9
- **Max calls in a function**: 2

**Interpretation**: moderate correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 362

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 342 (94.5%) |
| 1 call | 20 (5.5%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 352 (97.2%) |
| 1-10 objects | 10 (2.8%) |
| 11-50 objects | 0 (0.0%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__.iscoroutinefunction | 1 | 1 | 1 | 2 |
| __main__._make_timedelta | 1 | 1 | 1 | 2 |
| __main__.find_best_app | 1 | 1 | 9 | 2 |
| __main__.load_dotenv | 1 | 1 | 0 | 2 |
| __main__.show_server_banner | 1 | 1 | 0 | 2 |
| __main__._dump_loader_info | 1 | 1 | 1 | 2 |
| __main__.get_debug_flag | 1 | 1 | 0 | 3 |
| __main__.get_load_dotenv | 1 | 1 | 0 | 2 |
| __main__._prepare_send_file_kwargs | 1 | 1 | 0 | 3 |
| __main__._split_blueprint_path | 1 | 1 | 4 | 5 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.iscoroutinefunction | 1 | 1 | 1 | 2 |
| __main__._make_timedelta | 1 | 1 | 1 | 3 |
| __main__.find_best_app | 1 | 1 | 9 | 19 |
| __main__.load_dotenv | 1 | 1 | 0 | 0 |
| __main__.show_server_banner | 1 | 1 | 0 | 0 |
| __main__._dump_loader_info | 1 | 1 | 1 | 6 |
| __main__.get_debug_flag | 1 | 1 | 0 | 0 |
| __main__.get_load_dotenv | 1 | 1 | 0 | 0 |
| __main__._prepare_send_file_kwargs | 1 | 1 | 0 | 0 |
| __main__._split_blueprint_path | 1 | 1 | 4 | 8 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| __main__.find_best_app | 9 | 1 | 1 | 19 |
| find_best_app | 9 | 1 | 1 | 19 |
| __main__._split_blueprint_path | 4 | 1 | 1 | 8 |
| _split_blueprint_path | 4 | 1 | 1 | 8 |
| __main__.iscoroutinefunction | 1 | 1 | 1 | 2 |
| __main__._make_timedelta | 1 | 1 | 1 | 3 |
| __main__._dump_loader_info | 1 | 1 | 1 | 6 |
| iscoroutinefunction | 1 | 1 | 1 | 2 |
| _make_timedelta | 1 | 1 | 1 | 3 |
| _dump_loader_info | 1 | 1 | 1 | 6 |

## Memory Usage

- **Peak memory**: 39.43 MB
- **Current memory**: 36.26 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 2.01 | 1 | 63 |
| __main__.py | 1.94 | 0 | 2 |
| app.py | 3.38 | 78 | 1857 |
| blueprints.py | 2.66 | 28 | 578 |
| cli.py | 3.01 | 31 | 760 |
| config.py | 2.21 | 12 | 262 |
| ctx.py | 2.19 | 26 | 324 |
| debughelpers.py | 1.91 | 6 | 129 |
| globals.py | 2.00 | 6 | 87 |
| helpers.py | 2.66 | 21 | 532 |