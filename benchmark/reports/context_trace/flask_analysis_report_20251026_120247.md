# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-26T12:02:47.469292

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 0.02 seconds
- **Modules analyzed**: 1
- **Modules succeeded**: 1
- **Modules failed**: 0
- **Success rate**: 100.0%
- **Throughput**: 3197.7 LOC/sec

## Points-to Analysis Metrics

- **Total variables tracked**: 3
- **Non-empty points-to sets**: 3
- **Singleton sets**: 3 (100.0%)
- **Empty sets**: 0
- **Average set size**: 1.00
- **Maximum set size**: 1
- **Median set size**: 1.0

### Points-to Set Size Distribution

| Size Range | Count |
|------------|-------|
| 1-1 | 3 |

## Call Graph Metrics

- **Total functions**: 2
- **Total call edges**: 0
- **Average out-degree**: 0.00

## Object Metrics

- **Total objects created**: 2
- **Average objects per variable**: 1.00
- **Variables with no objects**: 0
- **Variables with singleton**: 3
- **Variables with multiple objects**: 0

### Object Type Distribution

| Type | Count |
|------|-------|
| globals | 1 |
| warnings | 1 |

## Object-Call Correlation Analysis

- **Pearson Correlation Coefficient**: 0.000
- **Functions with no calls**: 2
- **Functions with no objects**: 2
- **Average objects per function**: 0.0
- **Average calls per function**: 0.0
- **Max objects in a function**: 0
- **Max calls in a function**: 0

**Interpretation**: negligible correlation between object count and call edges

## Function Metrics

- **Total functions tracked**: 2

### Distribution by Outgoing Calls

| Call Count | Functions |
|------------|-----------|
| 0 calls | 2 (100.0%) |
| 1 call | 0 (0.0%) |
| 2-5 calls | 0 (0.0%) |
| 6-10 calls | 0 (0.0%) |
| 11+ calls | 0 (0.0%) |

### Distribution by Object Count

| Object Count | Functions |
|--------------|-----------|
| 0 objects | 2 (100.0%) |
| 1-10 objects | 0 (0.0%) |
| 11-50 objects | 0 (0.0%) |
| 51-100 objects | 0 (0.0%) |
| 101+ objects | 0 (0.0%) |

### Top Functions by Out-Degree (Most Calls)

| Function | Out-Degree | In-Degree | Objects | Contexts |
|----------|------------|-----------|---------|----------|
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |

### Top Functions by In-Degree (Most Called)

| Function | In-Degree | Out-Degree | Objects | Variables |
|----------|-----------|------------|---------|-----------|
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |

### Top Functions by Object Count

| Function | Objects | Out-Degree | In-Degree | Variables |
|----------|---------|------------|-----------|-----------|
| __getattr__ | 0 | 0 | 0 | 0 |
| __main__.__getattr__ | 0 | 0 | 0 | 0 |

## Memory Usage

- **Peak memory**: 2.34 MB
- **Current memory**: 1.53 MB

## Successfully Analyzed Modules

| Module | Duration (s) | Functions | LOC |
|--------|-------------|-----------|-----|
| __init__.py | 0.02 | 1 | 63 |