# QSS frozen-parameter sensitivity

This is a one-factor-at-a-time secondary robustness analysis. It does not select a new policy and must not be used to retune opened confirmatory results.

## Synthesis

- 115/132 nonbaseline case--variant comparisons (87.1%) retained the frozen baseline's exact, role, group, and group-loss-stage direction pattern.
- Excluding `tau_role`, which changes the evidence-role outcome definition rather than policy behavior, 100/108 comparisons (92.6%) retained the baseline pattern.
- Every observed frozen-group loss remained localized to exploration; late-pruning-only group losses: 0.
- `M=50`, both `tau_g` variants, `kappa=2`, and the 0.80/0.20 group-fit blend changed no direction/stage pattern. Reducing `M` to 20 changed four cases; `K=15` changed two; `kappa=4` and the 0.60/0.40 blend changed one each.
- Evidence-role conclusions were construct-threshold-sensitive: `tau_role=0.05` changed seven case patterns and `tau_role=0.15` changed two. Report this dependence rather than choosing the threshold with the most favorable outcomes.
- Gate counts are descriptive sensitivity results, not a parameter-selection objective. In particular, the greater number of passing rows at `M=20` or `K=15` must not be used to replace the frozen rule after confirmatory outcomes were opened.

## Visualization

![Case-by-variant sensitivity matrix and changed-case counts](../qss-failure-modes-manuscript/figures/parameter_sensitivity_case_matrix.png)

The matrix localizes each departure from the frozen baseline by case and outcome dimension (`E` exact-reference direction, `R` evidence-role direction, `G` frozen-group direction, and `S` first observed group-loss stage). Gold cells are changes caused by policy parameters; blue cells are changes caused by redefining the evidence-role outcome threshold. The summary bars count changed cases, not effect size or improvement.

## Variant-level pattern stability

| variant | evaluated rows | unchanged patterns | changed patterns |
|---|---:|---:|---:|
| Frozen v2 baseline | 12 | 12 | 0 |
| Exploration budget M=20 | 12 | 8 | 4 |
| Exploration budget M=50 | 12 | 12 | 0 |
| Final budget K=15 | 12 | 10 | 2 |
| Group gate tau_g=0.12 | 12 | 12 | 0 |
| Group gate tau_g=0.24 | 12 | 12 | 0 |
| Role similarity tau_role=0.05 | 12 | 5 | 7 |
| Role similarity tau_role=0.15 | 12 | 10 | 2 |
| Per-group first-pass capacity kappa=2 | 12 | 12 | 0 |
| Per-group first-pass capacity kappa=4 | 12 | 11 | 1 |
| Group fit semantic/lexical blend 0.60/0.40 | 12 | 11 | 1 |
| Group fit semantic/lexical blend 0.80/0.20 | 12 | 12 | 0 |

## Pattern changes relative to the frozen baseline

| variant | case | changed dimensions | exact | role | group | group-loss stage |
|---|---|---|---|---|---|---|
| group_blend_060 | C3-2023 | group_direction;group_loss_stage | tie | loss | loss | exploration |
| k15 | C2-2024 | role_direction | gain | gain | loss | exploration |
| k15 | C1-2024 | exact_direction | tie | gain | not_evaluable | none_or_not_localized |
| kappa_4 | C3-2024 | group_direction;group_loss_stage | tie | loss | loss | exploration |
| m20 | C2-2024 | role_direction | gain | gain | loss | exploration |
| m20 | C3-2023 | role_direction;group_direction;group_loss_stage | tie | gain | loss | exploration |
| m20 | C3-2024 | role_direction;group_direction;group_loss_stage | tie | gain | loss | exploration |
| m20 | C1-2024 | exact_direction | tie | gain | not_evaluable | none_or_not_localized |
| tau_role_005 | C2-2022 | role_direction | loss | loss | loss | exploration |
| tau_role_005 | C2-2024 | role_direction | gain | gain | loss | exploration |
| tau_role_005 | C3-2022 | role_direction | gain | tie | loss | exploration |
| tau_role_005 | C3-2023 | role_direction | tie | gain | tie | none_or_not_localized |
| tau_role_005 | C3-2024 | role_direction | tie | gain | tie | none_or_not_localized |
| tau_role_005 | C1-2024 | role_direction | loss | tie | not_evaluable | none_or_not_localized |
| tau_role_005 | C4-2024 | role_direction | gain | loss | loss | exploration |
| tau_role_015 | C2-2024 | role_direction | gain | tie | loss | exploration |
| tau_role_015 | C3-2022 | role_direction | gain | loss | loss | exploration |

Detailed machine-readable results are in `sensitivity_results.csv`; each run directory contains its request manifest, summary, per-bag results, and compact result note. Every evaluator summary must report `openalex_requests=0`.
