#!/usr/bin/env python3
"""Summarize how far recall differences moved at 75% candidate-pool visibility.

Sign counts alone cannot distinguish a reversal near zero from a large change.
This offline summary compares each 50-bag 75%-pool case-replicate with its
full-pool 50-bag baseline on the Figure 2 scale: mean exact hits divided by all
held-out references (exact) and mean evidence-role recall (role). Exact
comparisons require a reachable held-out reference in both pools. Values are
recursive-minus-fixed differences in percentage points.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_qss_parameter_sensitivity as parameter_sensitivity  # noqa: E402

BASELINE_RUNS = ROOT / "outputs/qss-parameter-sensitivity-v1/runs/baseline"
CONFIRMATION = ROOT / "outputs/qss-pool-size-sensitivity-confirmation-50bags-v1"
NEAR_ZERO_PP = 2.0
LARGE_PP = 10.0


def mean_hits(rows: list[dict[str, str]], policy: str) -> float:
    values = [float(row["exact_hits"]) for row in rows if row["policy"] == policy]
    return sum(values) / len(values)


def run_deltas(run_dir: Path) -> dict[str, float | None]:
    """Exact (all held-out denominator, as in Figure 2) and role deltas."""
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    policies = parameter_sensitivity.aggregate_by_policy(summary)
    fixed, late = policies["fixed_top10"], policies["late_pruned_top10"]
    exact = None
    if int(summary["reachable_holdout_count"]) > 0:
        with (run_dir / "per_bag_results.csv").open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        exact = (mean_hits(rows, "late_pruned_top10") - mean_hits(rows, "fixed_top10")) / int(
            summary["holdout_count"]
        )
    role = None
    if late.get("mean_group_recall") is not None and fixed.get("mean_group_recall") is not None:
        role = float(late["mean_group_recall"]) - float(fixed["mean_group_recall"])
    return {"exact": exact, "role": role}


def full_pool_deltas() -> dict[str, dict[str, float | None]]:
    return {path.name: run_deltas(path) for path in sorted(BASELINE_RUNS.iterdir()) if path.is_dir()}


def direction(value: float) -> str:
    return "gain" if value > 0 else "loss" if value < 0 else "tie"


def quantile(values: list[float], fraction: float) -> float:
    return statistics.quantiles(values, n=4, method="inclusive")[{0.25: 0, 0.75: 2}[fraction]]


def summarize(rows: list[dict[str, str]], full: dict, outcome: str) -> dict:
    changes, reversals = [], []
    for row in rows:
        before = full[row["case_id"]][outcome]
        after = row["reduced"][outcome]
        if before is None or after is None:
            continue
        before_pp, after_pp = 100 * before, 100 * after
        changes.append(abs(after_pp - before_pp))
        if {direction(before), direction(after)} == {"gain", "loss"}:
            reversals.append((before_pp, after_pp))
    sides = [max(abs(a), abs(b)) for a, b in reversals]
    return {
        "jointly_evaluable": len(changes),
        "median_abs_change_pp": round(statistics.median(changes), 2),
        "iqr_abs_change_pp": [round(quantile(changes, 0.25), 2), round(quantile(changes, 0.75), 2)],
        "max_abs_change_pp": round(max(changes), 2),
        "sign_reversals": len(reversals),
        "reversals_both_sides_within_2pp": sum(side <= NEAR_ZERO_PP for side in sides),
        "reversals_either_side_above_10pp": sum(side > LARGE_PP for side in sides),
        "median_abs_full_pool_pp_at_reversal": round(statistics.median(abs(a) for a, _ in reversals), 2),
        "median_abs_reduced_pool_pp_at_reversal": round(statistics.median(abs(b) for _, b in reversals), 2),
    }


def main() -> int:
    with (CONFIRMATION / "confirmation_results.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    full = full_pool_deltas()
    for row in rows:
        run_dir = CONFIRMATION / "runs" / row["case_id"] / "f075" / f"r{int(row['pool_replicate']):02d}"
        row["reduced"] = run_deltas(run_dir)
    result = {
        "definition": __doc__.strip().splitlines()[0],
        "scale": "recursive minus fixed, percentage points; exact recall uses all "
                 "held-out references as its denominator, as in Figure 2",
        "exact": summarize(rows, full, "exact"),
        "role": summarize(rows, full, "role"),
    }
    output = CONFIRMATION / "change_magnitude.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
