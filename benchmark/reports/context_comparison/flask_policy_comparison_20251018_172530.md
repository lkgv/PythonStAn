# Context Policy Comparison - FLASK

**Generated:** 2025-10-18T17:25:30.395752
**Project Path:** `/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask`
**Modules Analyzed:** 3

**Success Rate:** 1/1 (100%)

## Performance Comparison

| Policy | Duration (s) | Throughput (LOC/s) | Contexts | Vars/Context |
|--------|--------------|-------------------|----------|--------------|
| 2-cfa | 0.24 | 8149 | 0 | 0.0 |

## Precision Comparison

| Policy | Singleton % | Avg Size | Max Size | Total Vars |
|--------|-------------|----------|----------|------------|
| 2-cfa | 0.0% | 0.00 | 0 | 0 |

## Precision vs Performance Trade-off

| Policy | Precision Rank | Performance Rank | Combined Score |
|--------|---------------|------------------|----------------|
| 2-cfa | 1 | 1 | 1.0 |

## Recommendations

- **Best Precision:** 2-cfa (0.0% singleton)
- **Fastest:** 2-cfa (0.24s)
- **Best Balance:** 2-cfa (combined score: 1.0)

## Speedup vs 2-CFA (baseline)

| Policy | Speedup | Precision Gain |
|--------|---------|----------------|

## Detailed Statistics

| Policy | Functions | Classes | Modules | Status |
|--------|-----------|---------|---------|--------|
| 2-cfa | 0 | 0 | 0 | ✓ |