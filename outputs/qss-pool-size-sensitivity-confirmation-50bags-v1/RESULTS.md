# QSS 50-bag complete 75%-pool sensitivity

This complete confirmation reruns all 240 deterministic 75%-pool case-replicates with 50 bags, matching the frozen full-pool analysis.

## Confirmation summary

- 217/240 case-replicates retained the complete 10-bag screening pattern at 50 bags.
- 148/240 matched the frozen full-pool 50-bag pattern; 92 differed.
- Sign reversals versus the full-pool 50-bag result: exact 31, evidence-role 31, frozen-group 0.

| case | case-replicates | retained screening pattern | remained different from full pool |
|---|---:|---:|---:|
| C2-2022 | 20 | 19 | 11 |
| C2-2023 | 20 | 18 | 14 |
| C2-2024 | 20 | 14 | 16 |
| C3-2022 | 20 | 19 | 6 |
| C3-2023 | 20 | 18 | 6 |
| C3-2024 | 20 | 20 | 2 |
| C1-2022 | 20 | 20 | 8 |
| C1-2023 | 20 | 19 | 4 |
| C1-2024 | 20 | 13 | 13 |
| C4-2024 | 20 | 19 | 7 |
| C6-2024 | 20 | 18 | 5 |
| C5-2023 | 20 | 20 | 0 |

## Interpretation boundary

These are deterministic design-point frequencies, not sampling probabilities from a known retrieval population. Group definitions and assignment rules are frozen, but TF-IDF, semantic calibration, and record-level assignments are recomputed for each pool. The analysis varies pool size, membership, and representation together, without isolating their contributions or simulating retrieval rank. The 50-bag rerun uses the same data and is not independent validation.

The 240-row pattern counts include endpoints undefined in both comparisons: 80 exact, 40 role, and 60 group rows. Among rows with a defined full-pool endpoint, exact direction/evaluability matches in 90/160, role direction in 161/200, and group direction in 171/180. Twelve exact rows lose retrieval observability, leaving 148 jointly evaluable rows, of which 31 reverse sign. Role direction reverses in 31/200; group direction reverses in 0/180. A sign change alone does not quantify effect magnitude or substantive importance.
