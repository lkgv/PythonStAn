# FLASK - 2-CFA Pointer Analysis Report

**Generated:** 2025-10-17T21:10:42.361072

## Analysis Configuration

- **k**: 2 (call-string sensitivity)
- **obj_depth**: 2 (object sensitivity)
- **Field sensitivity**: attr-name
- **MRO enabled**: True
- **Class hierarchy**: True

## Results Summary

- **Duration**: 38.81 seconds
- **Modules analyzed**: 1
- **Modules succeeded**: 0
- **Modules failed**: 1
- **Success rate**: 0.0%
- **Throughput**: 0.0 LOC/sec

## Error Analysis

| Error Type | Count | Affected Modules |
|-----------|-------|------------------|
| AttributeError | 1 | __init__.py |

## Successfully Analyzed Modules


## Failed Modules

| Module | Error Type | Error Message |
|--------|-----------|---------------|
| __init__.py | AttributeError | 'KCFA2PointerAnalysis' object has no attribute 'so... |