# Context Policy Comparison - FLASK

**Generated:** 2025-10-18T17:27:43.887788
**Project Path:** `/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask`
**Modules Analyzed:** 21

**Success Rate:** 9/9 (100%)

## Performance Comparison

| Policy | Duration (s) | Throughput (LOC/s) | Contexts | Vars/Context |
|--------|--------------|-------------------|----------|--------------|
| 1-type | 1.00 | 6760 | 21 | 2.2 |
| 2-obj | 1.00 | 6736 | 21 | 2.2 |
| 1-obj | 1.00 | 6734 | 21 | 2.2 |
| 3-cfa | 1.02 | 6628 | 21 | 2.2 |
| 1-rcv | 1.02 | 6617 | 21 | 2.2 |
| 2-type | 1.02 | 6611 | 21 | 2.2 |
| 2-cfa | 1.03 | 6550 | 21 | 2.2 |
| 1-cfa | 1.06 | 6393 | 21 | 2.2 |
| 0-cfa | 1.06 | 6381 | 21 | 2.2 |

## Precision Comparison

| Policy | Singleton % | Avg Size | Max Size | Total Vars |
|--------|-------------|----------|----------|------------|
| 0-cfa | 100.0% | 1.00 | 1 | 47 |
| 1-cfa | 100.0% | 1.00 | 1 | 47 |
| 2-cfa | 100.0% | 1.00 | 1 | 47 |
| 3-cfa | 100.0% | 1.00 | 1 | 47 |
| 1-obj | 100.0% | 1.00 | 1 | 47 |
| 2-obj | 100.0% | 1.00 | 1 | 47 |
| 1-type | 100.0% | 1.00 | 1 | 47 |
| 2-type | 100.0% | 1.00 | 1 | 47 |
| 1-rcv | 100.0% | 1.00 | 1 | 47 |

## Precision vs Performance Trade-off

| Policy | Precision Rank | Performance Rank | Combined Score |
|--------|---------------|------------------|----------------|
| 3-cfa | 4 | 4 | 4.0 |
| 1-obj | 5 | 3 | 4.0 |
| 2-obj | 6 | 2 | 4.0 |
| 1-type | 7 | 1 | 4.0 |
| 0-cfa | 1 | 9 | 5.0 |
| 1-cfa | 2 | 8 | 5.0 |
| 2-cfa | 3 | 7 | 5.0 |
| 2-type | 8 | 6 | 7.0 |
| 1-rcv | 9 | 5 | 7.0 |

## Recommendations

- **Best Precision:** 0-cfa (100.0% singleton)
- **Fastest:** 1-type (1.00s)
- **Best Balance:** 3-cfa (combined score: 4.0)

## Speedup vs 0-CFA (baseline)

| Policy | Speedup | Precision Gain |
|--------|---------|----------------|
| 1-cfa | 1.00× | +0.0pp |
| 2-cfa | 1.03× | +0.0pp |
| 3-cfa | 1.04× | +0.0pp |
| 1-obj | 1.06× | +0.0pp |
| 2-obj | 1.06× | +0.0pp |
| 1-type | 1.06× | +0.0pp |
| 2-type | 1.04× | +0.0pp |
| 1-rcv | 1.04× | +0.0pp |

## Detailed Statistics

| Policy | Functions | Classes | Modules | Status |
|--------|-----------|---------|---------|--------|
| 0-cfa | 294 | 47 | 21 | ✓ |
| 1-cfa | 294 | 47 | 21 | ✓ |
| 2-cfa | 294 | 47 | 21 | ✓ |
| 3-cfa | 294 | 47 | 21 | ✓ |
| 1-obj | 294 | 47 | 21 | ✓ |
| 2-obj | 294 | 47 | 21 | ✓ |
| 1-type | 294 | 47 | 21 | ✓ |
| 2-type | 294 | 47 | 21 | ✓ |
| 1-rcv | 294 | 47 | 21 | ✓ |