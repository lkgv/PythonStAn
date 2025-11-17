# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-25T23:01:18.452332

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 6.44 seconds
- **Modules analyzed**: 22
- **Modules succeeded**: 22
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 1086.7 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 390
- **Non-empty points-to sets**: 390
- **Singleton sets**: 320 (82.1%)
- **Empty sets**: 0
- **Average set size**: 1.30
- **Maximum set size**: 12
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 320 |
| 11-20 | 1 |
| 2-5 | 69 |

## Call Graph Metrics

- **Total functions**: 305
- **Total call edges**: 25
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 51
- **Classes with MRO**: 51
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 285
- **Average objects per variable**: 1.30
- **Variables with no objects**: 0
- **Variables with singleton**: 320
- **Variables with multiple objects**: 70

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 212 |
| class | 48 |
| func | 25 |

## Function Metrics

- **Total functions tracked**: 16

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Contexts |
|----------|------------|-----------|----------|
| _make_timedelta | 1 | 1 | 2 |
| iscoroutinefunction | 1 | 1 | 2 |
| show_server_banner | 1 | 1 | 2 |
| find_best_app | 1 | 1 | 2 |
| load_dotenv | 1 | 1 | 2 |
| _dump_loader_info | 1 | 1 | 2 |
| _split_blueprint_path | 1 | 1 | 3 |
| _prepare_send_file_kwargs | 1 | 1 | 3 |
| htmlsafe_dumps | 1 | 1 | 2 |
| loads | 1 | 1 | 3 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Variables |
|----------|-----------|------------|-----------|
| _make_timedelta | 1 | 1 | 0 |
| iscoroutinefunction | 1 | 1 | 0 |
| show_server_banner | 1 | 1 | 0 |
| find_best_app | 1 | 1 | 8 |
| load_dotenv | 1 | 1 | 0 |
| _dump_loader_info | 1 | 1 | 0 |
| _split_blueprint_path | 1 | 1 | 4 |
| _prepare_send_file_kwargs | 1 | 1 | 0 |
| htmlsafe_dumps | 1 | 1 | 0 |
| loads | 1 | 1 | 8 |

## Memory Usage

- **Peak memory**: 34.05 MB
- **Current memory**: 26.25 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.03 | 1 | 63 |
| __main__.py | 0.00 | 0 | 2 |
| app.py | 1.48 | 78 | 1857 |
| blueprints.py | 0.64 | 28 | 578 |
| cli.py | 1.06 | 31 | 760 |
| config.py | 0.26 | 12 | 262 |
| ctx.py | 0.26 | 26 | 324 |
| debughelpers.py | 0.21 | 6 | 129 |
| globals.py | 0.07 | 6 | 87 |
| helpers.py | 0.32 | 21 | 532 |
| json/__init__.py | 0.18 | 10 | 259 |
| json/provider.py | 0.28 | 11 | 240 |
| json/tag.py | 0.25 | 33 | 219 |
| logging.py | 0.06 | 3 | 50 |
| scaffold.py | 0.35 | 36 | 620 |
| sessions.py | 0.29 | 22 | 294 |
| signals.py | 0.04 | 4 | 40 |
| templating.py | 0.20 | 14 | 166 |
| testing.py | 0.21 | 11 | 235 |
| typing.py | 0.06 | 0 | 56 |

*...and 2 more modules*