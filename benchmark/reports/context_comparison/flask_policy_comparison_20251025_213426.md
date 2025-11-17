# Context Policy Comparison - FLASK

**Generated:** 2025-10-25T21:34:26.774431
**Project Path:** `/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask`
**Modules Analyzed:** 3

**Success Rate:** 3/3 (100%)

## Performance Comparison

| Policy | Duration (s) | Throughput (LOC/s) | Contexts | Vars/Context |
|--------|--------------|-------------------|----------|--------------|
| 2-cfa | 0.23 | 8452 | 3 | 0.3 |
| 0-cfa | 0.23 | 8238 | 3 | 0.3 |
| 1-cfa | 0.24 | 8148 | 3 | 0.3 |

## Precision Comparison

| Policy | Singleton % | Avg Size | Max Size | Total Vars |
|--------|-------------|----------|----------|------------|
| 0-cfa | 100.0% | 1.00 | 1 | 1 |
| 1-cfa | 100.0% | 1.00 | 1 | 1 |
| 2-cfa | 100.0% | 1.00 | 1 | 1 |

## Precision vs Performance Trade-off

| Policy | Precision Rank | Performance Rank | Combined Score |
|--------|---------------|------------------|----------------|
| 0-cfa | 1 | 2 | 1.5 |
| 2-cfa | 3 | 1 | 2.0 |
| 1-cfa | 2 | 3 | 2.5 |

## Recommendations

- **Best Precision:** 0-cfa (100.0% singleton)
- **Fastest:** 2-cfa (0.23s)
- **Best Balance:** 0-cfa (combined score: 1.5)

## Speedup vs 0-CFA (baseline)

| Policy | Speedup | Precision Gain |
|--------|---------|----------------|
| 1-cfa | 0.99× | +0.0pp |
| 2-cfa | 1.03× | +0.0pp |

## Detailed Statistics

| Policy | Functions | Classes | Modules | Status |
|--------|-----------|---------|---------|--------|
| 0-cfa | 71 | 1 | 3 | ✓ |
| 1-cfa | 71 | 1 | 3 | ✓ |
| 2-cfa | 71 | 1 | 3 | ✓ |