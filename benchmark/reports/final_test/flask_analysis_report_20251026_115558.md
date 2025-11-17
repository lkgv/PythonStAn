# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T11:55:52.984937

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 4.34 seconds
- **Modules analyzed**: 10
- **Modules succeeded**: 0
- **Modules failed**: 10
- **Success rate**: 0.0%
- **Throughput**: 0.0 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 19
- **Non-empty points-to sets**: 19
- **Singleton sets**: 19 (100.0%)
- **Empty sets**: 0
- **Average set size**: 1.00
- **Maximum set size**: 1
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 19 |

## Call Graph Metrics

- **Total functions**: 362
- **Total call edges**: 0
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 19
- **Classes with MRO**: 19
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 183
- **Average objects per variable**: 1.00
- **Variables with no objects**: 0
- **Variables with singleton**: 19
- **Variables with multiple objects**: 0

### Object Type Distribution

| Type | Count |
|------|-------|
| class | 183 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.000
- **Functions with no calls**: 362
- **Functions with no objects**: 362
- **Average objects per function**: 0.0
- **Average calls per function**: 0.0
- **Max objects in a function**: 0
- **Max calls in a function**: 0

**Interpretation**: negligible correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 362

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 362 (100.0%) |
| 1 call | 0 (0.0%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 362 (100.0%) |
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

- **Peak memory**: 32.06 MB
- **Current memory**: 27.29 MB

## Error Analysis

| Error Type | Count | Affected Modules |
|-----------|-------|------------------|
| NameError | 10 | __init__.py, __main__.py, app.py (+7 more) |

## Successfully Analyzed Modules


## Failed Modules

| Module | Error Type | Error Message |
|--------|-----------|---------------|
| __init__.py | NameError | name 'IRImport' is not defined |
| __main__.py | NameError | name 'IRImport' is not defined |
| app.py | NameError | name 'IRImport' is not defined |
| blueprints.py | NameError | name 'IRImport' is not defined |
| cli.py | NameError | name 'IRImport' is not defined |
| config.py | NameError | name 'IRImport' is not defined |
| ctx.py | NameError | name 'IRImport' is not defined |
| debughelpers.py | NameError | name 'IRImport' is not defined |
| globals.py | NameError | name 'IRImport' is not defined |
| helpers.py | NameError | name 'IRImport' is not defined |