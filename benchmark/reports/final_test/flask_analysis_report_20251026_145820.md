# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T14:57:52.390097

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 27.63 seconds
- **Modules analyzed**: 5
- **Modules succeeded**: 5
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 118.0 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 4839
- **Non-empty points-to sets**: 4839
- **Singleton sets**: 4177 (86.3%)
- **Empty sets**: 0
- **Average set size**: 1.28
- **Maximum set size**: 37
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 4177 |
| 2-5 | 607 |
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

- **Total objects created**: 657
- **Average objects per variable**: 1.28
- **Variables with no objects**: 0
- **Variables with singleton**: 4177
- **Variables with multiple objects**: 662

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 325 |
| self | 128 |
| class | 124 |
| func | 34 |
| __main__.Blueprint.record_once | 10 |
| __main__.Flask.ensure_sync | 4 |
| __main__.Flask.jinja_env | 4 |
| __main__.Flask.debug | 4 |
| __main__.FlaskGroup._load_plugin_commands | 2 |
| __main__.Flask.request_context | 2 |
| __main__.Flask.wsgi_app | 1 |
| __main__.Flask.create_jinja_environment | 1 |
| __main__.Flask.async_to_sync | 1 |
| __main__.Flask.process_response | 1 |
| __main__.Flask.trap_http_exception | 1 |
| __main__.Flask.raise_routing_exception | 1 |
| __main__.Flask.logger | 1 |
| __main__.Flask.handle_http_exception | 1 |
| __main__.Flask.auto_find_instance_path | 1 |
| __main__.Blueprint.record | 1 |
| __main__.Flask.full_dispatch_request | 1 |
| __main__.Flask.make_response | 1 |
| __main__.Flask.make_aborter | 1 |
| __main__.Flask.finalize_request | 1 |
| __main__.Flask.make_default_options_response | 1 |
| __main__.Flask.select_jinja_autoescape | 1 |
| __main__.Flask.make_config | 1 |
| __main__.Flask.url_for | 1 |
| __main__.Flask.preprocess_request | 1 |
| __main__.Flask.dispatch_request | 1 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.547
- **Functions with no calls**: 229
- **Functions with no objects**: 241
- **Average objects per function**: 0.1
- **Average calls per function**: 0.1
- **Max objects in a function**: 11
- **Max calls in a function**: 5

**Interpretation**: strong correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 245

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 239 (97.6%) |
| 1 call | 5 (2.0%) |
| 2-5 calls | 1 (0.4%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 241 (98.4%) |
| 1-10 objects | 3 (1.2%) |
| 11-50 objects | 1 (0.4%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__ | 5 | 0 | 11 | 1 |
| __main__.Flask.send_file_max_age_default | 1 | 0 | 1 | 1 |
| __main__.Flask.ensure_sync | 1 | 0 | 1 | 1 |
| __main__.locate_app | 1 | 0 | 9 | 1 |
| __main__.run_command | 1 | 0 | 0 | 1 |
| __main__.FlaskGroup.make_context | 1 | 0 | 0 | 1 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.iscoroutinefunction | 0 | 2 | 0 | 2 |
| __main__._make_timedelta | 0 | 2 | 0 | 2 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.iscoroutinefunction | 2 | 0 | 0 | 0 |
| __main__._make_timedelta | 2 | 0 | 0 | 0 |
| __main__.find_best_app | 2 | 0 | 0 | 0 |
| __main__.load_dotenv | 2 | 0 | 0 | 0 |
| __main__.show_server_banner | 2 | 0 | 0 | 0 |
| iscoroutinefunction | 2 | 0 | 0 | 0 |
| _make_timedelta | 2 | 0 | 0 | 0 |
| find_best_app | 2 | 0 | 0 | 0 |
| load_dotenv | 2 | 0 | 0 | 0 |
| show_server_banner | 2 | 0 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| __main__ | 11 | 5 | 0 | 24 |
| __main__.locate_app | 9 | 1 | 0 | 19 |
| __main__.Flask.send_file_max_age_default | 1 | 1 | 0 | 3 |
| __main__.Flask.ensure_sync | 1 | 1 | 0 | 2 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |
| __main__.iscoroutinefunction | 0 | 0 | 2 | 0 |
| __main__._make_timedelta | 0 | 0 | 2 | 0 |
| __main__.Flask.session_cookie_name | 0 | 0 | 0 | 0 |
| __main__.Flask.use_x_sendfile | 0 | 0 | 0 | 0 |

## Memory Usage

- **Peak memory**: 40.04 MB
- **Current memory**: 40.01 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 5.02 | 1 | 63 |
| __main__.py | 4.89 | 0 | 2 |
| app.py | 6.27 | 78 | 1857 |
| blueprints.py | 5.48 | 28 | 578 |
| cli.py | 5.96 | 31 | 760 |