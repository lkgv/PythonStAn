# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T11:55:11.914263

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 1.41 seconds
- **Modules analyzed**: 3
- **Modules succeeded**: 0
- **Modules failed**: 3
- **Success rate**: 0.0%
- **Throughput**: 0.0 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 1
- **Non-empty points-to sets**: 1
- **Singleton sets**: 1 (100.0%)
- **Empty sets**: 0
- **Average set size**: 1.00
- **Maximum set size**: 1
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 1 |

## Call Graph Metrics

- **Total functions**: 143
- **Total call edges**: 0
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 1
- **Classes with MRO**: 1
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 69
- **Average objects per variable**: 1.00
- **Variables with no objects**: 0
- **Variables with singleton**: 1
- **Variables with multiple objects**: 0

### Object Type Distribution

| Type | Count |
|------|-------|
| class | 69 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.000
- **Functions with no calls**: 143
- **Functions with no objects**: 143
- **Average objects per function**: 0.0
- **Average calls per function**: 0.0
- **Max objects in a function**: 0
- **Max calls in a function**: 0

**Interpretation**: negligible correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 143

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 143 (100.0%) |
| 1 call | 0 (0.0%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 143 (100.0%) |
| 1-10 objects | 0 (0.0%) |
| 11-50 objects | 0 (0.0%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.iscoroutinefunction | 0 | 0 | 0 | 0 |
| __main__._make_timedelta | 0 | 0 | 0 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.send_file_max_age_default | 0 | 0 | 0 | 0 |
| __main__.Flask.use_x_sendfile | 0 | 0 | 0 | 0 |
| __main__.Flask.json_encoder | 0 | 0 | 0 | 0 |
| __main__.Flask.json_decoder | 0 | 0 | 0 | 0 |
| __main__.Flask.__init__ | 0 | 0 | 0 | 0 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.iscoroutinefunction | 0 | 0 | 0 | 0 |
| __main__._make_timedelta | 0 | 0 | 0 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.send_file_max_age_default | 0 | 0 | 0 | 0 |
| __main__.Flask.use_x_sendfile | 0 | 0 | 0 | 0 |
| __main__.Flask.json_encoder | 0 | 0 | 0 | 0 |
| __main__.Flask.json_decoder | 0 | 0 | 0 | 0 |
| __main__.Flask.__init__ | 0 | 0 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.iscoroutinefunction | 0 | 0 | 0 | 0 |
| __main__._make_timedelta | 0 | 0 | 0 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.send_file_max_age_default | 0 | 0 | 0 | 0 |
| __main__.Flask.use_x_sendfile | 0 | 0 | 0 | 0 |
| __main__.Flask.json_encoder | 0 | 0 | 0 | 0 |
| __main__.Flask.json_decoder | 0 | 0 | 0 | 0 |
| __main__.Flask.__init__ | 0 | 0 | 0 | 0 |

## Memory Usage

- **Peak memory**: 31.20 MB
- **Current memory**: 30.43 MB

## Error Analysis

| Error Type | Count | Affected Modules |
|-----------|-------|------------------|
| NameError | 3 | __init__.py, __main__.py, app.py |

## Successfully Analyzed Modules


## Failed Modules

| Module | Error Type | Error Message |
|--------|-----------|---------------|
| __init__.py | NameError | name 'IRImport' is not defined |
| __main__.py | NameError | name 'IRImport' is not defined |
| app.py | NameError | name 'IRImport' is not defined |