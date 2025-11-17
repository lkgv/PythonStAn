# Context Policy Comparison - FLASK

**Generated:** 2025-10-25T21:54:12.786737
**Project Path:** `/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask`
**Modules Analyzed:** 2

**Success Rate:** 2/2 (100%)

## Performance Comparison

| Policy | Duration (s) | Throughput (LOC/s) | Contexts | Vars/Context |
|--------|--------------|-------------------|----------|--------------|
| 2-cfa | 0.00 | 23005 | 2 | 0.0 |
| 0-cfa | 0.00 | 19155 | 2 | 0.0 |

## Precision Comparison

| Policy | Singleton % | Avg Size | Max Size | Total Vars |
|--------|-------------|----------|----------|------------|
| 0-cfa | 0.0% | 0.00 | 0 | 0 |
| 2-cfa | 0.0% | 0.00 | 0 | 0 |

## Precision vs Performance Trade-off

| Policy | Precision Rank | Performance Rank | Combined Score |
|--------|---------------|------------------|----------------|
| 0-cfa | 1 | 2 | 1.5 |
| 2-cfa | 2 | 1 | 1.5 |

## Recommendations

- **Best Precision:** 0-cfa (0.0% singleton)
- **Fastest:** 2-cfa (0.00s)
- **Best Balance:** 0-cfa (combined score: 1.5)

## Speedup vs 0-CFA (baseline)

| Policy | Speedup | Precision Gain |
|--------|---------|----------------|
| 2-cfa | 1.20× | +0.0pp |

## Detailed Statistics

| Policy | Functions | Classes | Modules | Status |
|--------|-----------|---------|---------|--------|
| 0-cfa | 1 | 0 | 2 | ✓ |
| 2-cfa | 1 | 0 | 2 | ✓ |