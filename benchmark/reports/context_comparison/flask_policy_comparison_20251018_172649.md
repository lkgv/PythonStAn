# Context Policy Comparison - FLASK

**Generated:** 2025-10-18T17:26:49.036457
**Project Path:** `/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask`
**Modules Analyzed:** 21

**Success Rate:** 3/3 (100%)

## Performance Comparison

| Policy | Duration (s) | Throughput (LOC/s) | Contexts | Vars/Context |
|--------|--------------|-------------------|----------|--------------|
| 2-cfa | 1.02 | 6611 | 21 | 2.2 |
| 1-cfa | 1.02 | 6608 | 21 | 2.2 |
| 0-cfa | 1.06 | 6381 | 21 | 2.2 |

## Precision Comparison

| Policy | Singleton % | Avg Size | Max Size | Total Vars |
|--------|-------------|----------|----------|------------|
| 0-cfa | 100.0% | 1.00 | 1 | 47 |
| 1-cfa | 100.0% | 1.00 | 1 | 47 |
| 2-cfa | 100.0% | 1.00 | 1 | 47 |

## Precision vs Performance Trade-off

| Policy | Precision Rank | Performance Rank | Combined Score |
|--------|---------------|------------------|----------------|
| 0-cfa | 1 | 3 | 2.0 |
| 1-cfa | 2 | 2 | 2.0 |
| 2-cfa | 3 | 1 | 2.0 |

## Recommendations

- **Best Precision:** 0-cfa (100.0% singleton)
- **Fastest:** 2-cfa (1.02s)
- **Best Balance:** 0-cfa (combined score: 2.0)

## Speedup vs 0-CFA (baseline)

| Policy | Speedup | Precision Gain |
|--------|---------|----------------|
| 1-cfa | 1.04× | +0.0pp |
| 2-cfa | 1.04× | +0.0pp |

## Detailed Statistics

| Policy | Functions | Classes | Modules | Status |
|--------|-----------|---------|---------|--------|
| 0-cfa | 294 | 47 | 21 | ✓ |
| 1-cfa | 294 | 47 | 21 | ✓ |
| 2-cfa | 294 | 47 | 21 | ✓ |