# Context Policy Comparison - FLASK

**Generated:** 2025-10-18T17:28:18.390595
**Project Path:** `/mnt/data_fast/code/PythonStAn/benchmark/projects/flask/src/flask`
**Modules Analyzed:** 21

**Success Rate:** 15/15 (100%)

## Performance Comparison

| Policy | Duration (s) | Throughput (LOC/s) | Contexts | Vars/Context |
|--------|--------------|-------------------|----------|--------------|
| 1-type | 1.00 | 6749 | 21 | 2.2 |
| 2c1o | 1.00 | 6746 | 21 | 2.2 |
| 1-obj | 1.00 | 6731 | 21 | 2.2 |
| 1c1o | 1.00 | 6729 | 21 | 2.2 |
| 3-cfa | 1.01 | 6725 | 21 | 2.2 |
| 3-rcv | 1.01 | 6715 | 21 | 2.2 |
| 2-obj | 1.01 | 6712 | 21 | 2.2 |
| 2-rcv | 1.01 | 6691 | 21 | 2.2 |
| 3-type | 1.01 | 6689 | 21 | 2.2 |
| 2-cfa | 1.01 | 6676 | 21 | 2.2 |
| 3-obj | 1.02 | 6652 | 21 | 2.2 |
| 1-cfa | 1.02 | 6641 | 21 | 2.2 |
| 1-rcv | 1.02 | 6629 | 21 | 2.2 |
| 2-type | 1.02 | 6612 | 21 | 2.2 |
| 0-cfa | 1.04 | 6495 | 21 | 2.2 |

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
| 3-obj | 100.0% | 1.00 | 1 | 47 |
| 3-type | 100.0% | 1.00 | 1 | 47 |
| 2-rcv | 100.0% | 1.00 | 1 | 47 |
| 3-rcv | 100.0% | 1.00 | 1 | 47 |
| 1c1o | 100.0% | 1.00 | 1 | 47 |
| 2c1o | 100.0% | 1.00 | 1 | 47 |

## Precision vs Performance Trade-off

| Policy | Precision Rank | Performance Rank | Combined Score |
|--------|---------------|------------------|----------------|
| 1-obj | 5 | 3 | 4.0 |
| 1-type | 7 | 1 | 4.0 |
| 3-cfa | 4 | 5 | 4.5 |
| 2-cfa | 3 | 10 | 6.5 |
| 2-obj | 6 | 7 | 6.5 |
| 1-cfa | 2 | 12 | 7.0 |
| 0-cfa | 1 | 15 | 8.0 |
| 2c1o | 15 | 2 | 8.5 |
| 1c1o | 14 | 4 | 9.0 |
| 3-rcv | 13 | 6 | 9.5 |
| 3-type | 11 | 9 | 10.0 |
| 2-rcv | 12 | 8 | 10.0 |
| 3-obj | 10 | 11 | 10.5 |
| 2-type | 8 | 14 | 11.0 |
| 1-rcv | 9 | 13 | 11.0 |

## Recommendations

- **Best Precision:** 0-cfa (100.0% singleton)
- **Fastest:** 1-type (1.00s)
- **Best Balance:** 1-obj (combined score: 4.0)

## Speedup vs 0-CFA (baseline)

| Policy | Speedup | Precision Gain |
|--------|---------|----------------|
| 1-cfa | 1.02× | +0.0pp |
| 2-cfa | 1.03× | +0.0pp |
| 3-cfa | 1.04× | +0.0pp |
| 1-obj | 1.04× | +0.0pp |
| 2-obj | 1.03× | +0.0pp |
| 1-type | 1.04× | +0.0pp |
| 2-type | 1.02× | +0.0pp |
| 1-rcv | 1.02× | +0.0pp |
| 3-obj | 1.02× | +0.0pp |
| 3-type | 1.03× | +0.0pp |
| 2-rcv | 1.03× | +0.0pp |
| 3-rcv | 1.03× | +0.0pp |
| 1c1o | 1.04× | +0.0pp |
| 2c1o | 1.04× | +0.0pp |

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
| 3-obj | 294 | 47 | 21 | ✓ |
| 3-type | 294 | 47 | 21 | ✓ |
| 2-rcv | 294 | 47 | 21 | ✓ |
| 3-rcv | 294 | 47 | 21 | ✓ |
| 1c1o | 294 | 47 | 21 | ✓ |
| 2c1o | 294 | 47 | 21 | ✓ |