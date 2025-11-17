# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T00:39:54.587415

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 5.47 seconds
- **Modules analyzed**: 15
- **Modules succeeded**: 15
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 1094.5 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 324
- **Non-empty points-to sets**: 324
- **Singleton sets**: 269 (83.0%)
- **Empty sets**: 0
- **Average set size**: 1.29
- **Maximum set size**: 12
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 269 |
| 11-20 | 1 |
| 2-5 | 54 |

## Call Graph Metrics

- **Total functions**: 249
- **Total call edges**: 21
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 35
- **Classes with MRO**: 35
- **Maximum MRO length**: 2
- **Average MRO length**: 2.00

## Object Metrics

- **Total objects created**: 235
- **Average objects per variable**: 1.29
- **Variables with no objects**: 0
- **Variables with singleton**: 269
- **Variables with multiple objects**: 55

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 180 |
| class | 35 |
| func | 20 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.581
- **Functions with no calls**: 198
- **Functions with no objects**: 206
- **Average objects per function**: 0.1
- **Average calls per function**: 0.1
- **Max objects in a function**: 5
- **Max calls in a function**: 2

**Interpretation**: strong correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 212

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 198 (93.4%) |
| 1 call | 14 (6.6%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 206 (97.2%) |
| 1-10 objects | 6 (2.8%) |
| 11-50 objects | 0 (0.0%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| iscoroutinefunction | 1 | 1 | 0 | 2 |
| _make_timedelta | 1 | 1 | 0 | 2 |
| find_best_app | 1 | 1 | 5 | 2 |
| load_dotenv | 1 | 1 | 0 | 2 |
| show_server_banner | 1 | 1 | 0 | 2 |
| _dump_loader_info | 1 | 1 | 0 | 2 |
| _prepare_send_file_kwargs | 1 | 1 | 0 | 3 |
| _split_blueprint_path | 1 | 1 | 2 | 3 |
| dumps | 1 | 1 | 5 | 3 |
| loads | 1 | 1 | 3 | 3 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| iscoroutinefunction | 1 | 1 | 0 | 0 |
| _make_timedelta | 1 | 1 | 0 | 0 |
| find_best_app | 1 | 1 | 5 | 8 |
| load_dotenv | 1 | 1 | 0 | 0 |
| show_server_banner | 1 | 1 | 0 | 0 |
| _dump_loader_info | 1 | 1 | 0 | 0 |
| _prepare_send_file_kwargs | 1 | 1 | 0 | 0 |
| _split_blueprint_path | 1 | 1 | 2 | 4 |
| dumps | 1 | 1 | 5 | 10 |
| loads | 1 | 1 | 3 | 8 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| find_best_app | 5 | 1 | 1 | 8 |
| dumps | 5 | 1 | 1 | 10 |
| loads | 3 | 1 | 1 | 8 |
| _find_package_path | 3 | 1 | 1 | 2 |
| _split_blueprint_path | 2 | 1 | 1 | 4 |
| has_level_handler | 1 | 1 | 1 | 1 |
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__ | 0 | 0 | 0 | 0 |
| iscoroutinefunction | 0 | 1 | 1 | 0 |
| _make_timedelta | 0 | 1 | 1 | 0 |

## Memory Usage

- **Peak memory**: 34.05 MB
- **Current memory**: 24.77 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.03 | 1 | 63 |
| __main__.py | 0.00 | 0 | 2 |
| app.py | 1.49 | 78 | 1857 |
| blueprints.py | 0.63 | 28 | 578 |
| cli.py | 1.07 | 31 | 760 |
| config.py | 0.26 | 12 | 262 |
| ctx.py | 0.26 | 26 | 324 |
| debughelpers.py | 0.22 | 6 | 129 |
| globals.py | 0.07 | 6 | 87 |
| helpers.py | 0.31 | 21 | 532 |
| json/__init__.py | 0.18 | 10 | 259 |
| json/provider.py | 0.27 | 11 | 240 |
| json/tag.py | 0.24 | 33 | 219 |
| logging.py | 0.06 | 3 | 50 |
| scaffold.py | 0.36 | 36 | 620 |