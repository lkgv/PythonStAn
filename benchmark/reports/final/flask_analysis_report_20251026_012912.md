# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T01:29:07.157399

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 4.65 seconds
- **Modules analyzed**: 10
- **Modules succeeded**: 0
- **Modules failed**: 10
- **Success rate**: 0.0%
- **Throughput**: 0.0 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 1157
- **Non-empty points-to sets**: 1157
- **Singleton sets**: 971 (83.9%)
- **Empty sets**: 0
- **Average set size**: 1.27
- **Maximum set size**: 9
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 971 |
| 2-5 | 176 |
| 6-10 | 10 |

## Call Graph Metrics

- **Total functions**: 2166
- **Total call edges**: 16
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 29
- **Classes with MRO**: 29
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 363
- **Average objects per variable**: 1.27
- **Variables with no objects**: 0
- **Variables with singleton**: 971
- **Variables with multiple objects**: 186

### Object Type Distribution

| Type | Count |
|------|-------|
| class | 184 |
| alloc | 164 |
| func | 15 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.445
- **Functions with no calls**: 357
- **Functions with no objects**: 361
- **Average objects per function**: 0.0
- **Average calls per function**: 0.0
- **Max objects in a function**: 2
- **Max calls in a function**: 2

**Interpretation**: moderate correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 362

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 357 (98.6%) |
| 1 call | 5 (1.4%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 361 (99.7%) |
| 1-10 objects | 1 (0.3%) |
| 11-50 objects | 0 (0.0%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__._make_timedelta | 1 | 1 | 0 | 2 |
| __main__.get_debug_flag | 1 | 1 | 0 | 2 |
| _make_timedelta | 1 | 1 | 0 | 2 |
| find_best_app | 1 | 1 | 2 | 2 |
| _dump_loader_info | 1 | 1 | 0 | 2 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.iscoroutinefunction | 0 | 0 | 0 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.send_file_max_age_default | 0 | 0 | 0 | 0 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__._make_timedelta | 1 | 1 | 0 | 0 |
| __main__.get_debug_flag | 1 | 1 | 0 | 0 |
| _make_timedelta | 1 | 1 | 0 | 0 |
| find_best_app | 1 | 1 | 2 | 2 |
| _dump_loader_info | 1 | 1 | 0 | 0 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.iscoroutinefunction | 0 | 0 | 0 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.send_file_max_age_default | 0 | 0 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| find_best_app | 2 | 1 | 1 | 2 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.iscoroutinefunction | 0 | 0 | 0 | 0 |
| __main__._make_timedelta | 0 | 1 | 1 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.send_file_max_age_default | 0 | 0 | 0 | 0 |
| __main__.Flask.use_x_sendfile | 0 | 0 | 0 | 0 |
| __main__.Flask.json_encoder | 0 | 0 | 0 | 0 |
| __main__.Flask.json_decoder | 0 | 0 | 0 | 0 |

## Memory Usage

- **Peak memory**: 37.29 MB
- **Current memory**: 35.65 MB

## Error Analysis

| Error Type | Count | Affected Modules |
|-----------|-------|------------------|
| TypeError | 10 | __init__.py, __main__.py, app.py (+7 more) |

## Successfully Analyzed Modules


## Failed Modules

| Module | Error Type | Error Message |
|--------|-----------|---------------|
| __init__.py | TypeError | 'frozenset' object is not subscriptable |
| __main__.py | TypeError | 'frozenset' object is not subscriptable |
| app.py | TypeError | 'frozenset' object is not subscriptable |
| blueprints.py | TypeError | 'frozenset' object is not subscriptable |
| cli.py | TypeError | 'frozenset' object is not subscriptable |
| config.py | TypeError | 'frozenset' object is not subscriptable |
| ctx.py | TypeError | 'frozenset' object is not subscriptable |
| debughelpers.py | TypeError | 'frozenset' object is not subscriptable |
| globals.py | TypeError | 'frozenset' object is not subscriptable |
| helpers.py | TypeError | 'frozenset' object is not subscriptable |