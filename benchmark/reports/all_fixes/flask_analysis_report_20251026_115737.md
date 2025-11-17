# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T11:56:22.265049

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 74.24 seconds
- **Modules analyzed**: 10
- **Modules succeeded**: 10
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 61.9 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 5149
- **Non-empty points-to sets**: 5149
- **Singleton sets**: 2508 (48.7%)
- **Empty sets**: 0
- **Average set size**: 4.78
- **Maximum set size**: 112
- **Median set size**: 2.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 2508 |
| 11-20 | 535 |
| 2-5 | 1211 |
| 21-50 | 120 |
| 51+ | 10 |
| 6-10 | 765 |

## Call Graph Metrics

- **Total functions**: 362
- **Total call edges**: 170
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 29
- **Classes with MRO**: 29
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 478
- **Average objects per variable**: 4.78
- **Variables with no objects**: 0
- **Variables with singleton**: 2508
- **Variables with multiple objects**: 2641

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 245 |
| class | 184 |
| warnings | 17 |
| func | 15 |
| Flask | 4 |
| dotenv | 3 |
| testing | 3 |
| ssl | 2 |
| readline | 1 |
| rlcompleter | 1 |
| globals | 1 |
| asgiref.sync | 1 |
| code | 1 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.509
- **Functions with no calls**: 342
- **Functions with no objects**: 350
- **Average objects per function**: 0.1
- **Average calls per function**: 0.1
- **Max objects in a function**: 10
- **Max calls in a function**: 2

**Interpretation**: strong correlation between object count and call edges

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
| 0 objects | 350 (96.7%) |
| 1-10 objects | 12 (3.3%) |
| 11-50 objects | 0 (0.0%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__.iscoroutinefunction | 1 | 1 | 1 | 2 |
| __main__._make_timedelta | 1 | 1 | 1 | 2 |
| __main__.find_best_app | 1 | 1 | 10 | 2 |
| __main__.load_dotenv | 1 | 1 | 1 | 2 |
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
| __main__.find_best_app | 1 | 1 | 10 | 20 |
| __main__.load_dotenv | 1 | 1 | 1 | 1 |
| __main__.show_server_banner | 1 | 1 | 0 | 0 |
| __main__._dump_loader_info | 1 | 1 | 1 | 6 |
| __main__.get_debug_flag | 1 | 1 | 0 | 0 |
| __main__.get_load_dotenv | 1 | 1 | 0 | 0 |
| __main__._prepare_send_file_kwargs | 1 | 1 | 0 | 0 |
| __main__._split_blueprint_path | 1 | 1 | 4 | 8 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| __main__.find_best_app | 10 | 1 | 1 | 20 |
| find_best_app | 10 | 1 | 1 | 20 |
| __main__._split_blueprint_path | 4 | 1 | 1 | 8 |
| _split_blueprint_path | 4 | 1 | 1 | 8 |
| __main__.iscoroutinefunction | 1 | 1 | 1 | 2 |
| __main__._make_timedelta | 1 | 1 | 1 | 3 |
| __main__.load_dotenv | 1 | 1 | 1 | 1 |
| __main__._dump_loader_info | 1 | 1 | 1 | 6 |
| iscoroutinefunction | 1 | 1 | 1 | 2 |
| _make_timedelta | 1 | 1 | 1 | 3 |

## Memory Usage

- **Peak memory**: 42.13 MB
- **Current memory**: 41.15 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 6.99 | 1 | 63 |
| __main__.py | 6.93 | 0 | 2 |
| app.py | 8.15 | 78 | 1857 |
| blueprints.py | 7.41 | 28 | 578 |
| cli.py | 7.79 | 31 | 760 |
| config.py | 7.17 | 12 | 262 |
| ctx.py | 7.57 | 26 | 324 |
| debughelpers.py | 6.51 | 6 | 129 |
| globals.py | 7.15 | 6 | 87 |
| helpers.py | 8.55 | 21 | 532 |