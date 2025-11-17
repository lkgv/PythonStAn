# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-25T22:59:14.042648

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 1.51 seconds
- **Modules analyzed**: 3
- **Modules succeeded**: 3
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 1271.6 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 91
- **Non-empty points-to sets**: 91
- **Singleton sets**: 75 (82.4%)
- **Empty sets**: 0
- **Average set size**: 1.38
- **Maximum set size**: 12
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 75 |
| 11-20 | 1 |
| 2-5 | 15 |

## Call Graph Metrics

- **Total functions**: 72
- **Total call edges**: 2
- **Average out-degree**: 0.00

## Class Hierarchy Metrics

- **Total classes**: 1
- **Classes with MRO**: 1
- **Maximum MRO length**: 3
- **Average MRO length**: 3.00

## Object Metrics

- **Total objects created**: 64
- **Average objects per variable**: 1.38
- **Variables with no objects**: 0
- **Variables with singleton**: 75
- **Variables with multiple objects**: 16

### Object Type Distribution

| Type | Count |
|------|-------|
| alloc | 60 |
| func | 3 |
| class | 1 |

## Function Metrics

- **Total functions tracked**: 2

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Contexts |
|----------|------------|-----------|----------|
| _make_timedelta | 1 | 1 | 2 |
| iscoroutinefunction | 1 | 1 | 2 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Variables |
|----------|-----------|------------|-----------|
| _make_timedelta | 1 | 1 | 0 |
| iscoroutinefunction | 1 | 1 | 0 |

## Memory Usage

- **Peak memory**: 34.05 MB
- **Current memory**: 33.22 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.03 | 1 | 63 |
| __main__.py | 0.00 | 0 | 2 |
| app.py | 1.48 | 78 | 1857 |