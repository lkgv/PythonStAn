# Context Policy Comparison - FLASK

**Generated:** 2025-10-25T22:27:57.578655
**Project Path:** `/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask`
**Modules Analyzed:** 3

**Success Rate:** 2/2 (100%)

## Performance Comparison

| Policy | Duration (s) | Throughput (LOC/s) | Contexts | Vars/Context |
|--------|--------------|-------------------|----------|--------------|
| 0-cfa | 0.24 | 8137 | 3 | 0.3 |
| 2-cfa | 0.24 | 7989 | 3 | 0.3 |

## Precision Comparison

| Policy | Singleton % | Avg Size | Max Size | Total Vars |
|--------|-------------|----------|----------|------------|
| 0-cfa | 100.0% | 1.00 | 1 | 1 |
| 2-cfa | 100.0% | 1.00 | 1 | 1 |

## Precision vs Performance Trade-off

| Policy | Precision Rank | Performance Rank | Combined Score |
|--------|---------------|------------------|----------------|
| 0-cfa | 1 | 1 | 1.0 |
| 2-cfa | 2 | 2 | 2.0 |

## Recommendations

- **Best Precision:** 0-cfa (100.0% singleton)
- **Fastest:** 0-cfa (0.24s)
- **Best Balance:** 0-cfa (combined score: 1.0)

## Speedup vs 0-CFA (baseline)

| Policy | Speedup | Precision Gain |
|--------|---------|----------------|
| 2-cfa | 0.98× | +0.0pp |

## Detailed Statistics

| Policy | Functions | Classes | Modules | Status |
|--------|-----------|---------|---------|--------|
| 0-cfa | 71 | 1 | 3 | ✓ |
| 2-cfa | 71 | 1 | 3 | ✓ |