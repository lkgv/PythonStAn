# Call Edge Quality Analysis Report

**Generated:** 2025-10-25T22:41:06.553045
**Source:** `test_with_calls.py`
**Policies Compared:** 0-cfa, 2-cfa

## Summary Comparison

| Policy | Call Sites | Call Edges | Resolution | Polymorphic | Contexts | Functions |
|--------|------------|------------|------------|-------------|----------|-----------|
| 0-cfa | 6 | 6 | 100.0% | 0.0% | 1 | 4 |
| 2-cfa | 8 | 8 | 100.0% | 0.0% | 9 | 4 |

## Call Site Resolution

| Policy | Total Sites | Resolved | Unresolved | Resolution Rate |
|--------|-------------|----------|------------|-----------------|
| 0-cfa | 6 | 6 | 0 | 100.0% |
| 2-cfa | 8 | 8 | 0 | 100.0% |

## Polymorphism Analysis

| Policy | Polymorphic Sites | Rate | Avg Targets | Max Targets |
|--------|------------------|------|-------------|-------------|
| 0-cfa | 0 | 0.0% | 0.00 | 0 |
| 2-cfa | 0 | 0.0% | 0.00 | 0 |

## Function Coverage

| Policy | Total Funcs | With Outgoing | With Incoming | Unreachable |
|--------|-------------|---------------|---------------|-------------|
| 0-cfa | 4 | 3 (75%) | 3 (75%) | 1 |
| 2-cfa | 4 | 3 (75%) | 3 (75%) | 1 |

## Degree Statistics

| Policy | Avg Out-Degree | Max Out-Degree | Avg In-Degree | Max In-Degree |
|--------|----------------|----------------|---------------|---------------|
| 0-cfa | 0.75 | 1 | 0.75 | 1 |
| 2-cfa | 0.75 | 1 | 0.75 | 1 |

## Out-Degree Distribution

| Policy | 0 | 1 |
|--------|---|---|
| 0-cfa | 1 | 3 |
| 2-cfa | 1 | 3 |

## Performance

| Policy | Analysis Time | Call Sites/sec |
|--------|---------------|----------------|
| 0-cfa | 0.00s | 1356.9 |
| 2-cfa | 0.00s | 1947.0 |

## Key Findings

### Context Sensitivity Impact (0-cfa vs 2-cfa)

- **Contexts:** 1 → 9 (**8** more contexts)
- **Call Edges:** 6 → 8 (**+33.3%**)
- **Resolution Rate:** 100.0% → 100.0% (**+0.0pp**)
- **Polymorphic Rate:** 0.0% → 0.0% (**+0.0pp**)