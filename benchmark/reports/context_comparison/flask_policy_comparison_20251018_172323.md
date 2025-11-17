# Context Policy Comparison - FLASK

**Generated:** 2025-10-18T17:23:23.921631
**Project Path:** `/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask`
**Modules Analyzed:** 21

**Success Rate:** 3/3 (100%)

## Performance Comparison

| Policy | Duration (s) | Throughput (LOC/s) | Contexts | Vars/Context |
|--------|--------------|-------------------|----------|--------------|
| 1-cfa | 1.00 | 6784 | 0 | 0.0 |
| 2-cfa | 1.00 | 6778 | 0 | 0.0 |
| 0-cfa | 1.06 | 6357 | 0 | 0.0 |

## Precision Comparison

| Policy | Singleton % | Avg Size | Max Size | Total Vars |
|--------|-------------|----------|----------|------------|
| 0-cfa | 0.0% | 0.00 | 0 | 0 |
| 1-cfa | 0.0% | 0.00 | 0 | 0 |
| 2-cfa | 0.0% | 0.00 | 0 | 0 |

## Precision vs Performance Trade-off

| Policy | Precision Rank | Performance Rank | Combined Score |
|--------|---------------|------------------|----------------|
| 1-cfa | 2 | 1 | 1.5 |
| 0-cfa | 1 | 3 | 2.0 |
| 2-cfa | 3 | 2 | 2.5 |

## Recommendations

- **Best Precision:** 0-cfa (0.0% singleton)
- **Fastest:** 1-cfa (1.00s)
- **Best Balance:** 1-cfa (combined score: 1.5)

## Speedup vs 0-CFA (baseline)

| Policy | Speedup | Precision Gain |
|--------|---------|----------------|
| 1-cfa | 1.07× | +0.0pp |
| 2-cfa | 1.07× | +0.0pp |

## Detailed Statistics

| Policy | Functions | Classes | Modules | Status |
|--------|-----------|---------|---------|--------|
| 0-cfa | 0 | 0 | 0 | ✓ |
| 1-cfa | 0 | 0 | 0 | ✓ |
| 2-cfa | 0 | 0 | 0 | ✓ |