# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T10:53:45.763684

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 7.07 seconds
- **Modules analyzed**: 22
- **Modules succeeded**: 22
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 989.3 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 742
- **Non-empty points-to sets**: 742
- **Singleton sets**: 630 (84.9%)
- **Empty sets**: 0
- **Average set size**: 1.27
- **Maximum set size**: 14
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 630 |
| 11-20 | 1 |
| 2-5 | 105 |
| 6-10 | 6 |

## Call Graph Metrics

- **Total functions**: 606
- **Total call edges**: 48
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 52
- **Classes with MRO**: 52
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 721
- **Average objects per variable**: 1.27
- **Variables with no objects**: 0
- **Variables with singleton**: 630
- **Variables with multiple objects**: 112

### Object Type Distribution

| Type | Count |
|------|-------|
| class | 369 |
| alloc | 323 |
| func | 29 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.505
- **Functions with no calls**: 573
- **Functions with no objects**: 584
- **Average objects per function**: 0.1
- **Average calls per function**: 0.1
- **Max objects in a function**: 18
- **Max calls in a function**: 2

**Interpretation**: strong correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 606

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 573 (94.6%) |
| 1 call | 33 (5.4%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 584 (96.4%) |
| 1-10 objects | 21 (3.5%) |
| 11-50 objects | 1 (0.2%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __main__.RequestContext.copy | 1 | 1 | 1 | 2 |
| __main__.attach_enctype_error_multidict | 1 | 1 | 1 | 2 |
| __main__._dump_loader_info | 1 | 1 | 1 | 2 |
| __main__.explain_template_loading_attempts | 1 | 1 | 18 | 2 |
| __main__.get_debug_flag | 1 | 1 | 0 | 2 |
| __main__.get_load_dotenv | 1 | 1 | 0 | 2 |
| __main__.stream_with_context | 1 | 1 | 10 | 6 |
| __main__.get_root_path | 1 | 1 | 1 | 2 |
| __main__._split_blueprint_path | 1 | 1 | 3 | 4 |
| __main__._default | 1 | 1 | 1 | 2 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.RequestContext.copy | 1 | 1 | 1 | 1 |
| __main__.attach_enctype_error_multidict | 1 | 1 | 1 | 2 |
| __main__._dump_loader_info | 1 | 1 | 1 | 6 |
| __main__.explain_template_loading_attempts | 1 | 1 | 18 | 22 |
| __main__.get_debug_flag | 1 | 1 | 0 | 0 |
| __main__.get_load_dotenv | 1 | 1 | 0 | 0 |
| __main__.stream_with_context | 1 | 1 | 10 | 16 |
| __main__.get_root_path | 1 | 1 | 1 | 3 |
| __main__._split_blueprint_path | 1 | 1 | 3 | 6 |
| __main__._default | 1 | 1 | 1 | 2 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| __main__.explain_template_loading_attempts | 18 | 1 | 1 | 22 |
| __main__.stream_with_context | 10 | 1 | 1 | 16 |
| find_best_app | 9 | 1 | 1 | 19 |
| dumps | 5 | 1 | 1 | 10 |
| __main__._split_blueprint_path | 3 | 1 | 1 | 6 |
| __main__._find_package_path | 3 | 1 | 1 | 2 |
| loads | 3 | 1 | 1 | 8 |
| _find_package_path | 3 | 1 | 1 | 2 |
| __main__.has_level_handler | 2 | 1 | 1 | 3 |
| _split_blueprint_path | 2 | 1 | 1 | 4 |

## Memory Usage

- **Peak memory**: 34.24 MB
- **Current memory**: 31.22 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.05 | 1 | 63 |
| __main__.py | 0.00 | 0 | 2 |
| app.py | 1.77 | 78 | 1857 |
| blueprints.py | 0.66 | 28 | 578 |
| cli.py | 1.18 | 31 | 760 |
| config.py | 0.26 | 12 | 262 |
| ctx.py | 0.28 | 26 | 324 |
| debughelpers.py | 0.23 | 6 | 129 |
| globals.py | 0.06 | 6 | 87 |
| helpers.py | 0.36 | 21 | 532 |
| json/__init__.py | 0.19 | 10 | 259 |
| json/provider.py | 0.24 | 11 | 240 |
| json/tag.py | 0.25 | 33 | 219 |
| logging.py | 0.07 | 3 | 50 |
| scaffold.py | 0.46 | 36 | 620 |
| sessions.py | 0.22 | 22 | 294 |
| signals.py | 0.03 | 4 | 40 |
| templating.py | 0.31 | 14 | 166 |
| testing.py | 0.23 | 11 | 235 |
| typing.py | 0.06 | 0 | 56 |

*...and 2 more modules*