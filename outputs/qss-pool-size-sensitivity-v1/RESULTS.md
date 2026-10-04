# QSS deterministic candidate-pool size sensitivity

This is a cache-only secondary robustness analysis under the frozen machine scope/group definitions. The source files do not preserve retrieval rank, so these results concern pool quantity and composition rather than a real retrieval top-N cutoff.

## Fraction-level stability

| pool fraction | case-replicates | complete pattern match vs full pool | exact endpoint lost | exact / role / group sign reversals | mean machine-eligible count | mean eligible rate |
|---:|---:|---:|---:|---:|---:|---:|
| 25% | 240 | 30/240 (12.5%) | 62 | 21 / 97 / 0 | 93.0 | 37.7% |
| 50% | 240 | 67/240 (27.9%) | 30 | 20 / 68 / 0 | 188.8 | 38.2% |
| 75% | 240 | 156/240 (65.0%) | 12 | 26 / 29 / 0 | 283.6 | 38.2% |
| 100% | 12 | 12/12 (100.0%) | 0 | 0 / 0 / 0 | 380.6 | 38.5% |

## Observed pattern changes

Across reduced pools, 467/720 deterministic case-replicates changed at least one direction, endpoint-evaluability state, or group-loss-stage label relative to the full pool.

| case | changed / reduced replicates | affected fractions | changed dimensions |
|---|---:|---|---|
| C2-2022 | 49/60 | 25%, 50%, 75% | exact_direction, group_direction, group_loss_stage, role_direction |
| C2-2023 | 53/60 | 25%, 50%, 75% | exact_direction, group_direction, group_loss_stage, role_direction |
| C2-2024 | 56/60 | 25%, 50%, 75% | exact_direction, group_direction, group_loss_stage, role_direction |
| C3-2022 | 43/60 | 25%, 50%, 75% | exact_direction, group_direction, group_loss_stage, role_direction |
| C3-2023 | 24/60 | 25%, 50%, 75% | group_direction, group_loss_stage, role_direction |
| C3-2024 | 25/60 | 25%, 50%, 75% | group_direction, group_loss_stage, role_direction |
| C1-2022 | 39/60 | 25%, 50%, 75% | exact_direction, role_direction |
| C1-2023 | 32/60 | 25%, 50%, 75% | exact_direction, role_direction |
| C1-2024 | 32/60 | 25%, 50%, 75% | exact_direction, role_direction |
| C4-2024 | 44/60 | 25%, 50%, 75% | exact_direction, group_direction, group_loss_stage, role_direction |
| C6-2024 | 45/60 | 25%, 50%, 75% | group_direction, group_loss_stage |
| C5-2023 | 25/60 | 25%, 50% | group_direction, group_loss_stage |

## Interpretation boundaries

- Replicates are deterministic design points, not random draws from a known retrieval population; their proportions are stability frequencies, not sampling probabilities.
- `machine-eligible` means the frozen automated scope/group gate passed. It is not a human or LLM judgment of substantive fit.
- A zero retrieval ceiling is reported but cannot diagnose ranking ability.
- Detailed results are in `pool_size_sensitivity_results.csv`; every evaluator run reports `openalex_requests=0`.
