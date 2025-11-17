# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T11:23:15.779310

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 78.48 seconds
- **Modules analyzed**: 10
- **Modules succeeded**: 10
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 58.5 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 5029
- **Non-empty points-to sets**: 5029
- **Singleton sets**: 2417 (48.1%)
- **Empty sets**: 0
- **Average set size**: 4.85
- **Maximum set size**: 110
- **Median set size**: 2.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 2417 |
| 11-20 | 520 |
| 2-5 | 1192 |
| 21-50 | 120 |
| 51+ | 10 |
| 6-10 | 770 |

## Call Graph Metrics

- **Total functions**: 362
- **Total call edges**: 170
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 29
- **Classes with MRO**: 29
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 461
- **Average objects per variable**: 4.85
- **Variables with no objects**: 0
- **Variables with singleton**: 2417
- **Variables with multiple objects**: 2612

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 245 |
| class | 184 |
| func | 15 |
| __main__.AppContext.__exit__ | 1 |
| __main__.AppContext.__enter__ | 1 |
| __main__.RequestContext.__repr__ | 1 |
| __main__.AppContext.push | 1 |
| __main__.BlueprintSetupState.add_url_rule | 1 |
| __main__.RequestContext.match_request | 1 |
| __main__.AppContext.__init__ | 1 |
| __main__.RequestContext.__enter__ | 1 |
| __main__.RequestContext.__exit__ | 1 |
| __main__.RequestContext.pop | 1 |
| __main__.RequestContext.copy | 1 |
| __main__.BlueprintSetupState.__init__ | 1 |
| __main__.RequestContext.push | 1 |
| __main__.ScriptInfo.__init__ | 1 |
| __main__.AppContext.pop | 1 |
| __main__.ScriptInfo.load_app | 1 |
| __main__.RequestContext.__init__ | 1 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.495
- **Functions with no calls**: 342
- **Functions with no objects**: 352
- **Average objects per function**: 0.1
- **Average calls per function**: 0.1
- **Max objects in a function**: 9
- **Max calls in a function**: 2

**Interpretation**: moderate correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 362

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 342 (94.5%) |
| 1 call | 20 (5.5%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 352 (97.2%) |
| 1-10 objects | 10 (2.8%) |
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
| __main__._dump_loader_info | 1 | 1 | 1 | 2 |
| __main__.get_debug_flag | 1 | 1 | 0 | 3 |
| __main__.get_load_dotenv | 1 | 1 | 0 | 2 |
| __main__._prepare_send_file_kwargs | 1 | 1 | 0 | 3 |
| __main__._split_blueprint_path | 1 | 1 | 4 | 5 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __main__.iscoroutinefunction | 1 | 1 | 1 | 2 |
| __main__._make_timedelta | 1 | 1 | 1 | 3 |
| __main__.find_best_app | 1 | 1 | 9 | 19 |
| __main__.load_dotenv | 1 | 1 | 0 | 0 |
| __main__.show_server_banner | 1 | 1 | 0 | 0 |
| __main__._dump_loader_info | 1 | 1 | 1 | 6 |
| __main__.get_debug_flag | 1 | 1 | 0 | 0 |
| __main__.get_load_dotenv | 1 | 1 | 0 | 0 |
| __main__._prepare_send_file_kwargs | 1 | 1 | 0 | 0 |
| __main__._split_blueprint_path | 1 | 1 | 4 | 8 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| __main__.find_best_app | 9 | 1 | 1 | 19 |
| find_best_app | 9 | 1 | 1 | 19 |
| __main__._split_blueprint_path | 4 | 1 | 1 | 8 |
| _split_blueprint_path | 4 | 1 | 1 | 8 |
| __main__.iscoroutinefunction | 1 | 1 | 1 | 2 |
| __main__._make_timedelta | 1 | 1 | 1 | 3 |
| __main__._dump_loader_info | 1 | 1 | 1 | 6 |
| iscoroutinefunction | 1 | 1 | 1 | 2 |
| _make_timedelta | 1 | 1 | 1 | 3 |
| _dump_loader_info | 1 | 1 | 1 | 6 |

## Memory Usage

- **Peak memory**: 42.17 MB
- **Current memory**: 41.20 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 7.61 | 1 | 63 |
| __main__.py | 7.74 | 0 | 2 |
| app.py | 8.78 | 78 | 1857 |
| blueprints.py | 8.45 | 28 | 578 |
| cli.py | 7.93 | 31 | 760 |
| config.py | 7.74 | 12 | 262 |
| ctx.py | 8.48 | 26 | 324 |
| debughelpers.py | 6.95 | 6 | 129 |
| globals.py | 7.22 | 6 | 87 |
| helpers.py | 7.58 | 21 | 532 |