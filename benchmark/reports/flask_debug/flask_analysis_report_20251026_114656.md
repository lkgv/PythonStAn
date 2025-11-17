# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T11:46:54.119260

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 2.02 seconds
- **Modules analyzed**: 3
- **Modules succeeded**: 3
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 953.8 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 466
- **Non-empty points-to sets**: 466
- **Singleton sets**: 345 (74.0%)
- **Empty sets**: 0
- **Average set size**: 1.57
- **Maximum set size**: 13
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 345 |
| 11-20 | 3 |
| 2-5 | 103 |
| 6-10 | 15 |

## Call Graph Metrics

- **Total functions**: 143
- **Total call edges**: 6
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 1
- **Classes with MRO**: 1
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 167
- **Average objects per variable**: 1.57
- **Variables with no objects**: 0
- **Variables with singleton**: 345
- **Variables with multiple objects**: 121

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 95 |
| class | 69 |
| func | 3 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 1.000
- **Functions with no calls**: 139
- **Functions with no objects**: 139
- **Average objects per function**: 0.0
- **Average calls per function**: 0.1
- **Max objects in a function**: 1
- **Max calls in a function**: 2

**Interpretation**: very strong correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 143

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 139 (97.2%) |
| 1 call | 4 (2.8%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 139 (97.2%) |
| 1-10 objects | 4 (2.8%) |
| 11-50 objects | 0 (0.0%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__.iscoroutinefunction | 1 | 1 | 1 | 2 |
| __main__._make_timedelta | 1 | 1 | 1 | 2 |
| iscoroutinefunction | 1 | 1 | 1 | 2 |
| _make_timedelta | 1 | 1 | 1 | 2 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.send_file_max_age_default | 0 | 0 | 0 | 0 |
| __main__.Flask.use_x_sendfile | 0 | 0 | 0 | 0 |
| __main__.Flask.json_encoder | 0 | 0 | 0 | 0 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.iscoroutinefunction | 1 | 1 | 1 | 2 |
| __main__._make_timedelta | 1 | 1 | 1 | 3 |
| iscoroutinefunction | 1 | 1 | 1 | 2 |
| _make_timedelta | 1 | 1 | 1 | 3 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.send_file_max_age_default | 0 | 0 | 0 | 0 |
| __main__.Flask.use_x_sendfile | 0 | 0 | 0 | 0 |
| __main__.Flask.json_encoder | 0 | 0 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| __main__.iscoroutinefunction | 1 | 1 | 1 | 2 |
| __main__._make_timedelta | 1 | 1 | 1 | 3 |
| iscoroutinefunction | 1 | 1 | 1 | 2 |
| _make_timedelta | 1 | 1 | 1 | 3 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.send_file_max_age_default | 0 | 0 | 0 | 0 |
| __main__.Flask.use_x_sendfile | 0 | 0 | 0 | 0 |
| __main__.Flask.json_encoder | 0 | 0 | 0 | 0 |

## Memory Usage

- **Peak memory**: 31.63 MB
- **Current memory**: 31.27 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.22 | 1 | 63 |
| __main__.py | 0.20 | 0 | 2 |
| app.py | 1.60 | 78 | 1857 |