# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T10:52:23.609659

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 4.73 seconds
- **Modules analyzed**: 10
- **Modules succeeded**: 10
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 971.2 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 447
- **Non-empty points-to sets**: 447
- **Singleton sets**: 372 (83.2%)
- **Empty sets**: 0
- **Average set size**: 1.31
- **Maximum set size**: 13
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 372 |
| 11-20 | 1 |
| 2-5 | 68 |
| 6-10 | 6 |

## Call Graph Metrics

- **Total functions**: 362
- **Total call edges**: 18
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 20
- **Classes with MRO**: 20
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 412
- **Average objects per variable**: 1.31
- **Variables with no objects**: 0
- **Variables with singleton**: 372
- **Variables with multiple objects**: 75

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 213 |
| class | 184 |
| func | 15 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.515
- **Functions with no calls**: 351
- **Functions with no objects**: 356
- **Average objects per function**: 0.0
- **Average calls per function**: 0.1
- **Max objects in a function**: 9
- **Max calls in a function**: 2

**Interpretation**: strong correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 362

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 351 (97.0%) |
| 1 call | 11 (3.0%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 356 (98.3%) |
| 1-10 objects | 6 (1.7%) |
| 11-50 objects | 0 (0.0%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__.get_debug_flag | 1 | 1 | 0 | 2 |
| __main__.get_load_dotenv | 1 | 1 | 0 | 2 |
| __main__._split_blueprint_path | 1 | 1 | 3 | 4 |
| iscoroutinefunction | 1 | 1 | 1 | 2 |
| _make_timedelta | 1 | 1 | 1 | 2 |
| find_best_app | 1 | 1 | 9 | 2 |
| load_dotenv | 1 | 1 | 0 | 2 |
| show_server_banner | 1 | 1 | 0 | 2 |
| _dump_loader_info | 1 | 1 | 1 | 2 |
| _prepare_send_file_kwargs | 1 | 1 | 0 | 3 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.get_debug_flag | 1 | 1 | 0 | 0 |
| __main__.get_load_dotenv | 1 | 1 | 0 | 0 |
| __main__._split_blueprint_path | 1 | 1 | 3 | 6 |
| iscoroutinefunction | 1 | 1 | 1 | 2 |
| _make_timedelta | 1 | 1 | 1 | 3 |
| find_best_app | 1 | 1 | 9 | 19 |
| load_dotenv | 1 | 1 | 0 | 0 |
| show_server_banner | 1 | 1 | 0 | 0 |
| _dump_loader_info | 1 | 1 | 1 | 6 |
| _prepare_send_file_kwargs | 1 | 1 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| find_best_app | 9 | 1 | 1 | 19 |
| __main__._split_blueprint_path | 3 | 1 | 1 | 6 |
| _split_blueprint_path | 2 | 1 | 1 | 4 |
| iscoroutinefunction | 1 | 1 | 1 | 2 |
| _make_timedelta | 1 | 1 | 1 | 3 |
| _dump_loader_info | 1 | 1 | 1 | 6 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.iscoroutinefunction | 0 | 0 | 0 | 0 |
| __main__._make_timedelta | 0 | 0 | 0 | 0 |

## Memory Usage

- **Peak memory**: 32.19 MB
- **Current memory**: 28.07 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.04 | 1 | 63 |
| __main__.py | 0.00 | 0 | 2 |
| app.py | 1.68 | 78 | 1857 |
| blueprints.py | 0.65 | 28 | 578 |
| cli.py | 1.15 | 31 | 760 |
| config.py | 0.25 | 12 | 262 |
| ctx.py | 0.32 | 26 | 324 |
| debughelpers.py | 0.23 | 6 | 129 |
| globals.py | 0.06 | 6 | 87 |
| helpers.py | 0.35 | 21 | 532 |