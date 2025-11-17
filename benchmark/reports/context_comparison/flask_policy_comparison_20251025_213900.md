# Context Policy Comparison - FLASK

**Generated:** 2025-10-25T21:39:00.273551
**Project Path:** `/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask`
**Modules Analyzed:** 2

**Success Rate:** 3/3 (100%)

## Performance Comparison

| Policy | Duration (s) | Throughput (LOC/s) | Contexts | Vars/Context |
|--------|--------------|-------------------|----------|--------------|
| 2-cfa | 0.00 | 22310 | 2 | 0.0 |
| 1-cfa | 0.00 | 21703 | 2 | 0.0 |
| 0-cfa | 0.00 | 17414 | 2 | 0.0 |

## Precision Comparison

| Policy | Singleton % | Avg Size | Max Size | Total Vars |
|--------|-------------|----------|----------|------------|
| 0-cfa | 0.0% | 0.00 | 0 | 0 |
| 1-cfa | 0.0% | 0.00 | 0 | 0 |
| 2-cfa | 0.0% | 0.00 | 0 | 0 |

## Precision vs Performance Trade-off

| Policy | Precision Rank | Performance Rank | Combined Score |
|--------|---------------|------------------|----------------|
| 0-cfa | 1 | 3 | 2.0 |
| 1-cfa | 2 | 2 | 2.0 |
| 2-cfa | 3 | 1 | 2.0 |

## Recommendations

- **Best Precision:** 0-cfa (0.0% singleton)
- **Fastest:** 2-cfa (0.00s)
- **Best Balance:** 0-cfa (combined score: 2.0)

## Speedup vs 0-CFA (baseline)

| Policy | Speedup | Precision Gain |
|--------|---------|----------------|
| 1-cfa | 1.25× | +0.0pp |
| 2-cfa | 1.28× | +0.0pp |

## Detailed Statistics

| Policy | Functions | Classes | Modules | Status |
|--------|-----------|---------|---------|--------|
| 0-cfa | 1 | 0 | 2 | ✓ |
| 1-cfa | 1 | 0 | 2 | ✓ |
| 2-cfa | 1 | 0 | 2 | ✓ |