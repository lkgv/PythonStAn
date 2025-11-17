# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T11:08:45.679673

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 18.13 seconds
- **Modules analyzed**: 5
- **Modules succeeded**: 5
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 179.9 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 2245
- **Non-empty points-to sets**: 2245
- **Singleton sets**: 1645 (73.3%)
- **Empty sets**: 0
- **Average set size**: 1.58
- **Maximum set size**: 36
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 1645 |
| 2-5 | 545 |
| 21-50 | 5 |
| 6-10 | 50 |

## Call Graph Metrics

- **Total functions**: 245
- **Total call edges**: 459
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 9
- **Classes with MRO**: 9
- **Maximum MRO length**: 4
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 385
- **Average objects per variable**: 1.58
- **Variables with no objects**: 0
- **Variables with singleton**: 1645
- **Variables with multiple objects**: 600

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 239 |
| class | 124 |
| func | 22 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.537
- **Functions with no calls**: 215
- **Functions with no objects**: 227
- **Average objects per function**: 0.5
- **Average calls per function**: 0.2
- **Max objects in a function**: 23
- **Max calls in a function**: 2

**Interpretation**: strong correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 245

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 215 (87.8%) |
| 1 call | 30 (12.2%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 227 (92.7%) |
| 1-10 objects | 14 (5.7%) |
| 11-50 objects | 4 (1.6%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__.iscoroutinefunction | 1 | 1 | 8 | 17 |
| __main__._make_timedelta | 1 | 1 | 1 | 2 |
| __main__.Flask.make_aborter | 1 | 1 | 0 | 2 |
| __main__.Flask.auto_find_instance_path | 1 | 1 | 2 | 2 |
| __main__.Flask.create_jinja_environment | 1 | 1 | 2 | 2 |
| __main__.Flask.handle_http_exception | 1 | 1 | 0 | 2 |
| __main__.Flask.trap_http_exception | 1 | 1 | 1 | 2 |
| __main__.Flask.raise_routing_exception | 1 | 1 | 6 | 5 |
| __main__.Flask.dispatch_request | 1 | 1 | 2 | 5 |
| __main__.Flask.full_dispatch_request | 1 | 1 | 0 | 4 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.iscoroutinefunction | 1 | 1 | 8 | 16 |
| __main__._make_timedelta | 1 | 1 | 1 | 3 |
| __main__.Flask.make_aborter | 1 | 1 | 0 | 0 |
| __main__.Flask.auto_find_instance_path | 1 | 1 | 2 | 1 |
| __main__.Flask.create_jinja_environment | 1 | 1 | 2 | 2 |
| __main__.Flask.handle_http_exception | 1 | 1 | 0 | 0 |
| __main__.Flask.trap_http_exception | 1 | 1 | 1 | 1 |
| __main__.Flask.raise_routing_exception | 1 | 1 | 6 | 10 |
| __main__.Flask.dispatch_request | 1 | 1 | 2 | 6 |
| __main__.Flask.full_dispatch_request | 1 | 1 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| __main__.Flask.preprocess_request | 23 | 1 | 1 | 32 |
| __main__.Flask.process_response | 18 | 1 | 1 | 22 |
| __main__.Flask.make_response | 12 | 1 | 1 | 18 |
| __main__.Blueprint.record_once | 12 | 1 | 1 | 16 |
| __main__.find_best_app | 9 | 1 | 1 | 19 |
| find_best_app | 9 | 1 | 1 | 19 |
| __main__.iscoroutinefunction | 8 | 1 | 1 | 16 |
| __main__.Flask.raise_routing_exception | 6 | 1 | 1 | 10 |
| iscoroutinefunction | 6 | 1 | 1 | 12 |
| __main__.Flask.request_context | 3 | 1 | 1 | 9 |

## Memory Usage

- **Peak memory**: 37.00 MB
- **Current memory**: 36.72 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 3.15 | 1 | 63 |
| __main__.py | 3.11 | 0 | 2 |
| app.py | 4.31 | 78 | 1857 |
| blueprints.py | 3.57 | 28 | 578 |
| cli.py | 3.99 | 31 | 760 |