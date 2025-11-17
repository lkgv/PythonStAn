# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T14:49:14.435130

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 27.36 seconds
- **Modules analyzed**: 5
- **Modules succeeded**: 5
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 119.1 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 4839
- **Non-empty points-to sets**: 4839
- **Singleton sets**: 4170 (86.2%)
- **Empty sets**: 0
- **Average set size**: 1.28
- **Maximum set size**: 36
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 4170 |
| 2-5 | 614 |
| 21-50 | 5 |
| 6-10 | 50 |

## Call Graph Metrics

- **Total functions**: 245
- **Total call edges**: 50
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 9
- **Classes with MRO**: 9
- **Maximum MRO length**: 4
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 679
- **Average objects per variable**: 1.28
- **Variables with no objects**: 0
- **Variables with singleton**: 4170
- **Variables with multiple objects**: 669

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 347 |
| self | 128 |
| class | 124 |
| func | 34 |
| __main__.Blueprint.record_once | 10 |
| __main__.Flask.debug | 4 |
| __main__.Flask.jinja_env | 4 |
| __main__.Flask.ensure_sync | 4 |
| __main__.FlaskGroup._load_plugin_commands | 2 |
| __main__.Flask.request_context | 2 |
| __main__.Flask.make_aborter | 1 |
| __main__.Flask.create_jinja_environment | 1 |
| __main__.Flask.preprocess_request | 1 |
| __main__.Flask.finalize_request | 1 |
| __main__.Flask.auto_find_instance_path | 1 |
| __main__.Flask.select_jinja_autoescape | 1 |
| __main__.Flask.make_response | 1 |
| __main__.Flask.raise_routing_exception | 1 |
| __main__.Flask.async_to_sync | 1 |
| __main__.Flask.full_dispatch_request | 1 |
| __main__.Flask.make_config | 1 |
| __main__.Flask.wsgi_app | 1 |
| __main__.Flask.process_response | 1 |
| __main__.Flask.make_default_options_response | 1 |
| __main__.Flask.dispatch_request | 1 |
| __main__.Flask.url_for | 1 |
| __main__.Flask.logger | 1 |
| __main__.Blueprint.record | 1 |
| __main__.Flask.trap_http_exception | 1 |
| __main__.Flask.handle_http_exception | 1 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.532
- **Functions with no calls**: 235
- **Functions with no objects**: 239
- **Average objects per function**: 0.2
- **Average calls per function**: 0.1
- **Max objects in a function**: 18
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
| 1-10 objects | 4 (1.6%) |
| 11-50 objects | 2 (0.8%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__.iscoroutinefunction | 1 | 1 | 2 | 4 |
| __main__._make_timedelta | 1 | 1 | 2 | 4 |
| __main__.find_best_app | 1 | 1 | 18 | 4 |
| __main__.load_dotenv | 1 | 1 | 0 | 4 |
| __main__.show_server_banner | 1 | 1 | 0 | 4 |
| iscoroutinefunction | 1 | 1 | 2 | 4 |
| _make_timedelta | 1 | 1 | 2 | 4 |
| find_best_app | 1 | 1 | 18 | 4 |
| load_dotenv | 1 | 1 | 0 | 4 |
| show_server_banner | 1 | 1 | 0 | 4 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.iscoroutinefunction | 1 | 1 | 2 | 4 |
| __main__._make_timedelta | 1 | 1 | 2 | 6 |
| __main__.find_best_app | 1 | 1 | 18 | 38 |
| __main__.load_dotenv | 1 | 1 | 0 | 0 |
| __main__.show_server_banner | 1 | 1 | 0 | 0 |
| iscoroutinefunction | 1 | 1 | 2 | 4 |
| _make_timedelta | 1 | 1 | 2 | 6 |
| find_best_app | 1 | 1 | 18 | 38 |
| load_dotenv | 1 | 1 | 0 | 0 |
| show_server_banner | 1 | 1 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| __main__.find_best_app | 18 | 1 | 1 | 38 |
| find_best_app | 18 | 1 | 1 | 38 |
| __main__.iscoroutinefunction | 2 | 1 | 1 | 4 |
| __main__._make_timedelta | 2 | 1 | 1 | 6 |
| iscoroutinefunction | 2 | 1 | 1 | 4 |
| _make_timedelta | 2 | 1 | 1 | 6 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.send_file_max_age_default | 0 | 0 | 0 | 0 |

## Memory Usage

- **Peak memory**: 40.03 MB
- **Current memory**: 40.01 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 5.29 | 1 | 63 |
| __main__.py | 5.16 | 0 | 2 |
| app.py | 6.42 | 78 | 1857 |
| blueprints.py | 4.47 | 28 | 578 |
| cli.py | 6.01 | 31 | 760 |