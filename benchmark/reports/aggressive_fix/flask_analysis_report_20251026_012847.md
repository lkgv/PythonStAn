# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T01:28:43.078811

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 3.22 seconds
- **Modules analyzed**: 5
- **Modules succeeded**: 0
- **Modules failed**: 5
- **Success rate**: 0.0%
- **Throughput**: 0.0 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 614
- **Non-empty points-to sets**: 614
- **Singleton sets**: 526 (85.7%)
- **Empty sets**: 0
- **Average set size**: 1.24
- **Maximum set size**: 9
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 526 |
| 2-5 | 83 |
| 6-10 | 5 |

## Call Graph Metrics

- **Total functions**: 764
- **Total call edges**: 11
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 9
- **Classes with MRO**: 9
- **Maximum MRO length**: 4
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 281
- **Average objects per variable**: 1.24
- **Variables with no objects**: 0
- **Variables with singleton**: 526
- **Variables with multiple objects**: 88

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 143 |
| class | 124 |
| func | 14 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.373
- **Functions with no calls**: 238
- **Functions with no objects**: 244
- **Average objects per function**: 0.0
- **Average calls per function**: 0.1
- **Max objects in a function**: 2
- **Max calls in a function**: 2

**Interpretation**: moderate correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 245

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 238 (97.1%) |
| 1 call | 7 (2.9%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 244 (99.6%) |
| 1-10 objects | 1 (0.4%) |
| 11-50 objects | 0 (0.0%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__.iscoroutinefunction | 1 | 1 | 0 | 2 |
| __main__._make_timedelta | 1 | 1 | 0 | 2 |
| iscoroutinefunction | 1 | 1 | 0 | 2 |
| _make_timedelta | 1 | 1 | 0 | 2 |
| find_best_app | 1 | 1 | 2 | 2 |
| load_dotenv | 1 | 1 | 0 | 2 |
| show_server_banner | 1 | 1 | 0 | 2 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.iscoroutinefunction | 1 | 1 | 0 | 0 |
| __main__._make_timedelta | 1 | 1 | 0 | 0 |
| iscoroutinefunction | 1 | 1 | 0 | 0 |
| _make_timedelta | 1 | 1 | 0 | 0 |
| find_best_app | 1 | 1 | 2 | 2 |
| load_dotenv | 1 | 1 | 0 | 0 |
| show_server_banner | 1 | 1 | 0 | 0 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| find_best_app | 2 | 1 | 1 | 2 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.iscoroutinefunction | 0 | 1 | 1 | 0 |
| __main__._make_timedelta | 0 | 1 | 1 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.send_file_max_age_default | 0 | 0 | 0 | 0 |
| __main__.Flask.use_x_sendfile | 0 | 0 | 0 | 0 |
| __main__.Flask.json_encoder | 0 | 0 | 0 | 0 |
| __main__.Flask.json_decoder | 0 | 0 | 0 | 0 |

## Memory Usage

- **Peak memory**: 33.91 MB
- **Current memory**: 32.88 MB

## Error Analysis

| Error Type | Count | Affected Modules |
|-----------|-------|------------------|
| TypeError | 5 | __init__.py, __main__.py, app.py (+2 more) |

## Successfully Analyzed Modules


## Failed Modules

| Module | Error Type | Error Message |
|--------|-----------|---------------|
| __init__.py | TypeError | 'frozenset' object is not subscriptable |
| __main__.py | TypeError | 'frozenset' object is not subscriptable |
| app.py | TypeError | 'frozenset' object is not subscriptable |
| blueprints.py | TypeError | 'frozenset' object is not subscriptable |
| cli.py | TypeError | 'frozenset' object is not subscriptable |