# Context Policy Comparison - FLASK

**Generated:** 2025-10-25T21:42:24.128410
**Project Path:** `/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask`
**Modules Analyzed:** 5

**Success Rate:** 5/5 (100%)

## Performance Comparison

| Policy | Duration (s) | Throughput (LOC/s) | Contexts | Vars/Context |
|--------|--------------|-------------------|----------|--------------|
| 2-cfa | 0.52 | 6267 | 10 | 15.3 |
| 1-type | 0.52 | 6243 | 10 | 15.3 |
| 1-obj | 0.52 | 6235 | 10 | 15.3 |
| 1-cfa | 0.53 | 6176 | 10 | 15.3 |
| 0-cfa | 0.53 | 6146 | 5 | 30.0 |

## Precision Comparison

| Policy | Singleton % | Avg Size | Max Size | Total Vars |
|--------|-------------|----------|----------|------------|
| 1-cfa | 84.3% | 1.20 | 4 | 153 |
| 2-cfa | 84.3% | 1.20 | 4 | 153 |
| 1-obj | 84.3% | 1.20 | 4 | 153 |
| 1-type | 84.3% | 1.20 | 4 | 153 |
| 0-cfa | 84.0% | 1.20 | 4 | 150 |

## Precision vs Performance Trade-off

| Policy | Precision Rank | Performance Rank | Combined Score |
|--------|---------------|------------------|----------------|
| 2-cfa | 2 | 1 | 1.5 |
| 1-cfa | 1 | 4 | 2.5 |
| 1-obj | 3 | 3 | 3.0 |
| 1-type | 4 | 2 | 3.0 |
| 0-cfa | 5 | 5 | 5.0 |

## Recommendations

- **Best Precision:** 1-cfa (84.3% singleton)
- **Fastest:** 2-cfa (0.52s)
- **Best Balance:** 2-cfa (combined score: 1.5)

## Speedup vs 0-CFA (baseline)

| Policy | Speedup | Precision Gain |
|--------|---------|----------------|
| 1-cfa | 1.00× | +0.3pp |
| 2-cfa | 1.02× | +0.3pp |
| 1-obj | 1.01× | +0.3pp |
| 1-type | 1.02× | +0.3pp |

## Detailed Statistics

| Policy | Functions | Classes | Modules | Status |
|--------|-----------|---------|---------|--------|
| 0-cfa | 128 | 9 | 5 | ✓ |
| 1-cfa | 128 | 9 | 5 | ✓ |
| 2-cfa | 128 | 9 | 5 | ✓ |
| 1-obj | 128 | 9 | 5 | ✓ |
| 1-type | 128 | 9 | 5 | ✓ |