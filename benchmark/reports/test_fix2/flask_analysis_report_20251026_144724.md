# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T14:47:17.092970

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 6.94 seconds
- **Modules analyzed**: 5
- **Modules succeeded**: 5
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 469.5 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 1579
- **Non-empty points-to sets**: 1579
- **Singleton sets**: 1118 (70.8%)
- **Empty sets**: 0
- **Average set size**: 1.74
- **Maximum set size**: 37
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 1118 |
| 2-5 | 406 |
| 21-50 | 5 |
| 6-10 | 50 |

## Call Graph Metrics

- **Total functions**: 245
- **Total call edges**: 25
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 9
- **Classes with MRO**: 9
- **Maximum MRO length**: 4
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 301
- **Average objects per variable**: 1.74
- **Variables with no objects**: 0
- **Variables with singleton**: 1118
- **Variables with multiple objects**: 461

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 163 |
| class | 124 |
| func | 14 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.532
- **Functions with no calls**: 235
- **Functions with no objects**: 239
- **Average objects per function**: 0.1
- **Average calls per function**: 0.1
- **Max objects in a function**: 9
- **Max calls in a function**: 2

**Interpretation**: strong correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 245

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 235 (95.9%) |
| 1 call | 10 (4.1%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 239 (97.6%) |
| 1-10 objects | 6 (2.4%) |
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
| iscoroutinefunction | 1 | 1 | 1 | 2 |
| _make_timedelta | 1 | 1 | 1 | 2 |
| find_best_app | 1 | 1 | 9 | 2 |
| load_dotenv | 1 | 1 | 0 | 2 |
| show_server_banner | 1 | 1 | 0 | 2 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.iscoroutinefunction | 1 | 1 | 1 | 2 |
| __main__._make_timedelta | 1 | 1 | 1 | 3 |
| __main__.find_best_app | 1 | 1 | 9 | 19 |
| __main__.load_dotenv | 1 | 1 | 0 | 0 |
| __main__.show_server_banner | 1 | 1 | 0 | 0 |
| iscoroutinefunction | 1 | 1 | 1 | 2 |
| _make_timedelta | 1 | 1 | 1 | 3 |
| find_best_app | 1 | 1 | 9 | 19 |
| load_dotenv | 1 | 1 | 0 | 0 |
| show_server_banner | 1 | 1 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| __main__.find_best_app | 9 | 1 | 1 | 19 |
| find_best_app | 9 | 1 | 1 | 19 |
| __main__.iscoroutinefunction | 1 | 1 | 1 | 2 |
| __main__._make_timedelta | 1 | 1 | 1 | 3 |
| iscoroutinefunction | 1 | 1 | 1 | 2 |
| _make_timedelta | 1 | 1 | 1 | 3 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.send_file_max_age_default | 0 | 0 | 0 | 0 |

## Memory Usage

- **Peak memory**: 34.58 MB
- **Current memory**: 33.68 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.85 | 1 | 63 |
| __main__.py | 0.81 | 0 | 2 |
| app.py | 2.23 | 78 | 1857 |
| blueprints.py | 1.22 | 28 | 578 |
| cli.py | 1.83 | 31 | 760 |