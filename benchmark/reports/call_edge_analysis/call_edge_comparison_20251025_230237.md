# Call Edge Quality Analysis Report

**Generated:** 2025-10-25T23:02:37.920948
**Source:** `benchmark/projects/flask/src/flask`
**Policies Compared:** 0-cfa, 1-cfa, 2-cfa

## Summary Comparison

| Policy | Call Sites | Call Edges | Resolution | Polymorphic | Contexts | Functions |
|--------|------------|------------|------------|-------------|----------|-----------|
| 0-cfa | 19 | 19 | 100.0% | 0.0% | 18 | 293 |
| 1-cfa | 22 | 22 | 100.0% | 0.0% | 37 | 293 |
| 2-cfa | 25 | 25 | 100.0% | 0.0% | 40 | 293 |

## Call Site Resolution

| Policy | Total Sites | Resolved | Unresolved | Resolution Rate |
|--------|-------------|----------|------------|-----------------|
| 0-cfa | 19 | 19 | 0 | 100.0% |
| 1-cfa | 22 | 22 | 0 | 100.0% |
| 2-cfa | 25 | 25 | 0 | 100.0% |

## Polymorphism Analysis

| Policy | Polymorphic Sites | Rate | Avg Targets | Max Targets |
|--------|------------------|------|-------------|-------------|
| 0-cfa | 0 | 0.0% | 0.00 | 0 |
| 1-cfa | 0 | 0.0% | 0.00 | 0 |
| 2-cfa | 0 | 0.0% | 0.00 | 0 |

## Function Coverage

| Policy | Total Funcs | With Outgoing | With Incoming | Unreachable |
|--------|-------------|---------------|---------------|-------------|
| 0-cfa | 293 | 16 (5%) | 16 (5%) | 277 |
| 1-cfa | 293 | 16 (5%) | 16 (5%) | 277 |
| 2-cfa | 293 | 16 (5%) | 16 (5%) | 277 |

## Degree Statistics

| Policy | Avg Out-Degree | Max Out-Degree | Avg In-Degree | Max In-Degree |
|--------|----------------|----------------|---------------|---------------|
| 0-cfa | 0.06 | 1 | 0.06 | 1 |
| 1-cfa | 0.06 | 1 | 0.06 | 1 |
| 2-cfa | 0.06 | 1 | 0.06 | 1 |

## Out-Degree Distribution

| Policy | 0 | 1 |
|--------|---|---|
| 0-cfa | 231 | 16 |
| 1-cfa | 231 | 16 |
| 2-cfa | 231 | 16 |

## Performance

| Policy | Analysis Time | Call Sites/sec |
|--------|---------------|----------------|
| 0-cfa | 1.10s | 17.3 |
| 1-cfa | 1.08s | 20.4 |
| 2-cfa | 1.06s | 23.6 |

## Key Findings

### Context Sensitivity Impact (0-cfa vs 2-cfa)

- **Contexts:** 18 → 40 (**22** more contexts)
- **Call Edges:** 19 → 25 (**+31.6%**)
- **Resolution Rate:** 100.0% → 100.0% (**+0.0pp**)
- **Polymorphic Rate:** 0.0% → 0.0% (**+0.0pp**)