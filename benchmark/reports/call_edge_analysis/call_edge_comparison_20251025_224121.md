# Call Edge Quality Analysis Report

**Generated:** 2025-10-25T22:41:21.605640
**Source:** `benchmark/projects/flask/src/flask`
**Policies Compared:** 0-cfa, 2-cfa

## Summary Comparison

| Policy | Call Sites | Call Edges | Resolution | Polymorphic | Contexts | Functions |
|--------|------------|------------|------------|-------------|----------|-----------|
| 0-cfa | 2 | 2 | 100.0% | 0.0% | 2 | 71 |
| 2-cfa | 2 | 2 | 100.0% | 0.0% | 4 | 71 |

## Call Site Resolution

| Policy | Total Sites | Resolved | Unresolved | Resolution Rate |
|--------|-------------|----------|------------|-----------------|
| 0-cfa | 2 | 2 | 0 | 100.0% |
| 2-cfa | 2 | 2 | 0 | 100.0% |

## Polymorphism Analysis

| Policy | Polymorphic Sites | Rate | Avg Targets | Max Targets |
|--------|------------------|------|-------------|-------------|
| 0-cfa | 0 | 0.0% | 0.00 | 0 |
| 2-cfa | 0 | 0.0% | 0.00 | 0 |

## Function Coverage

| Policy | Total Funcs | With Outgoing | With Incoming | Unreachable |
|--------|-------------|---------------|---------------|-------------|
| 0-cfa | 71 | 2 (3%) | 2 (3%) | 69 |
| 2-cfa | 71 | 2 (3%) | 2 (3%) | 69 |

## Degree Statistics

| Policy | Avg Out-Degree | Max Out-Degree | Avg In-Degree | Max In-Degree |
|--------|----------------|----------------|---------------|---------------|
| 0-cfa | 0.03 | 1 | 0.03 | 1 |
| 2-cfa | 0.03 | 1 | 0.03 | 1 |

## Out-Degree Distribution

| Policy | 0 | 1 |
|--------|---|---|
| 0-cfa | 69 | 2 |
| 2-cfa | 69 | 2 |

## Performance

| Policy | Analysis Time | Call Sites/sec |
|--------|---------------|----------------|
| 0-cfa | 0.28s | 7.1 |
| 2-cfa | 0.28s | 7.1 |

## Key Findings

### Context Sensitivity Impact (0-cfa vs 2-cfa)

- **Contexts:** 2 → 4 (**2** more contexts)
- **Call Edges:** 2 → 2 (**+0.0%**)
- **Resolution Rate:** 100.0% → 100.0% (**+0.0pp**)
- **Polymorphic Rate:** 0.0% → 0.0% (**+0.0pp**)