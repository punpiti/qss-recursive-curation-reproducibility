#!/usr/bin/env python3
"""Evaluate one frozen text-and-bagging late-pruner on a development cutoff."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_qss_recursive_experiment as qss


_WORKER_SPACE: Any = None
_WORKER_OPTIONS: dict[str, Any] = {}
_WORKER_LIMITER: Any = None


def progress(current: int, total: int, label: str) -> None:
    width = 30
    filled = int(width * current / total) if total else width
    print(
        f"\r{label} [{'#' * filled}{'.' * (width - filled)}] {current}/{total}",
        end="",
        flush=True,
    )
    if current == total:
        print()


def percentile_scores(values: list[float]) -> list[float]:
    """Return tie-aware [0, 1] percentiles with higher values scoring higher."""
    if not values:
        return []
    if len(values) == 1:
        return [1.0]
    positions: dict[float, list[int]] = defaultdict(list)
    for position, index in enumerate(sorted(range(len(values)), key=values.__getitem__)):
        positions[values[index]].append(position)
    result = [0.0] * len(values)
    denominator = len(values) - 1
    for value, ranks in positions.items():
        score = statistics.fmean(ranks) / denominator
        for index, item in enumerate(values):
            if item == value:
                result[index] = score
    return result


def leave_one_out_confidence(selection_count: int, bags: int) -> float:
    if bags <= 1:
        return 1.0
    return max(0.0, min(1.0, (selection_count - 1) / (bags - 1)))


def late_prune(
    decisions: list[dict[str, Any]],
    selection_counts: Counter[int],
    bags: int,
    group_capacities: dict[str, int],
    tau_fit: float,
    final_k: int,
) -> tuple[list[int], list[dict[str, Any]]]:
    """Apply the frozen equal-rank score and final scope/capacity gate."""
    rows = []
    for decision in decisions:
        row = dict(decision)
        row["bagging_confidence_loo"] = leave_one_out_confidence(
            selection_counts[int(row["candidate_index"])], bags
        )
        rows.append(row)
    components = (
        ("relevance", "relevance_percentile"),
        ("concept_novelty_gain", "concept_novelty_gain_percentile"),
        ("bagging_confidence_loo", "bagging_confidence_percentile"),
    )
    for source, destination in components:
        for row, score in zip(rows, percentile_scores([float(row[source]) for row in rows])):
            row[destination] = score
    for row in rows:
        row["late_pruning_score"] = statistics.fmean(
            float(row[destination]) for _, destination in components
        )
        row["passes_final_scope_gate"] = bool(
            row.get("group") is not None and float(row.get("group_fit", 0.0)) >= tau_fit
        )

    ranked = sorted(
        (row for row in rows if row["passes_final_scope_gate"]),
        key=lambda row: (
            float(row["late_pruning_score"]),
            float(row["bagging_confidence_loo"]),
            float(row["relevance"]),
            float(row["concept_novelty_gain"]),
            float(row["utility"]),
            str(row.get("title", "")),
        ),
        reverse=True,
    )
    selected: list[int] = []
    group_counts: Counter[str] = Counter()
    for row in ranked:
        group = str(row["group"])
        if group_counts[group] >= group_capacities[group]:
            row["final_decision"] = "capacity_rejected"
            continue
        row["final_decision"] = "selected"
        row["final_rank"] = len(selected) + 1
        selected.append(int(row["candidate_index"]))
        group_counts[group] += 1
        if len(selected) >= final_k:
            break
    selected_set = set(selected)
    for row in rows:
        if int(row["candidate_index"]) in selected_set:
            continue
        if not row["passes_final_scope_gate"]:
            row["final_decision"] = "scope_rejected"
        elif "final_decision" not in row:
            row["final_decision"] = "below_final_cut"
    rows.sort(key=lambda row: (
        0 if row.get("final_decision") == "selected" else 1,
        int(row.get("final_rank", 10**9)),
        -float(row["late_pruning_score"]),
        str(row.get("title", "")),
    ))
    return selected, rows


def late_prune_relevance_soft_capacity(
    decisions: list[dict[str, Any]],
    selection_counts: Counter[int],
    bags: int,
    group_capacities: dict[str, int],
    tau_fit: float,
    final_k: int,
) -> tuple[list[int], list[dict[str, Any]]]:
    """Rank by text relevance, then use capacity as a first-pass preference.

    Novelty and bagging confidence are deterministic tie-breakers rather than
    equal-weight penalties.  This prevents confidence from structurally
    suppressing candidates that emerge only after the recursive profile moves.
    """
    rows = []
    for decision in decisions:
        row = dict(decision)
        row["bagging_confidence_loo"] = leave_one_out_confidence(
            selection_counts[int(row["candidate_index"])], bags
        )
        row["passes_final_scope_gate"] = bool(
            row.get("group") is not None and float(row.get("group_fit", 0.0)) >= tau_fit
        )
        row["late_pruning_score"] = float(row["relevance"])
        rows.append(row)
    ranked = sorted(
        (row for row in rows if row["passes_final_scope_gate"]),
        key=lambda row: (
            float(row["relevance"]),
            float(row["concept_novelty_gain"]),
            float(row["bagging_confidence_loo"]),
            float(row["utility"]),
            str(row.get("title", "")),
        ),
        reverse=True,
    )
    chosen: list[dict[str, Any]] = []
    group_counts: Counter[str] = Counter()
    for row in ranked:
        group = str(row["group"])
        if group_counts[group] >= group_capacities[group]:
            continue
        row["selection_phase"] = "diversity_first_pass"
        chosen.append(row)
        group_counts[group] += 1
        if len(chosen) >= final_k:
            break
    if len(chosen) < final_k:
        chosen_ids = {int(row["candidate_index"]) for row in chosen}
        for row in ranked:
            if int(row["candidate_index"]) in chosen_ids:
                continue
            row["selection_phase"] = "relevance_backfill"
            chosen.append(row)
            chosen_ids.add(int(row["candidate_index"]))
            if len(chosen) >= final_k:
                break
    selected = [int(row["candidate_index"]) for row in chosen]
    selected_set = set(selected)
    for rank, row in enumerate(chosen, start=1):
        row["final_decision"] = "selected"
        row["final_rank"] = rank
    for row in rows:
        if int(row["candidate_index"]) in selected_set:
            continue
        row["final_decision"] = (
            "scope_rejected" if not row["passes_final_scope_gate"] else "below_final_cut"
        )
    rows.sort(key=lambda row: (
        0 if row.get("final_decision") == "selected" else 1,
        int(row.get("final_rank", 10**9)),
        -float(row["late_pruning_score"]),
        str(row.get("title", "")),
    ))
    return selected, rows


def initialize_worker(space: Any, options: dict[str, Any]) -> None:
    global _WORKER_SPACE, _WORKER_OPTIONS, _WORKER_LIMITER
    _WORKER_SPACE = space
    _WORKER_OPTIONS = options
    _WORKER_LIMITER = threadpool_limits(limits=1)
    _WORKER_LIMITER.__enter__()


def exploration_task(task: tuple[float, int]) -> dict[str, Any]:
    fraction, bag = task
    space = _WORKER_SPACE
    options = _WORKER_OPTIONS
    rng = random.Random(
        options["random_seed"]
        + space.cutoff * 10000
        + int(fraction * 1000) * 100
        + bag
    )
    seed_indices = qss.stratified_sample_indices(space.past, fraction, rng)
    fixed, _, _, _ = qss.run_condition(
        space,
        seed_indices,
        "fixed",
        options["tau_fit"],
        options["tau_gain"],
        options["max_rounds"],
        options["final_k"],
        max_total_additions=options["final_k"],
    )
    expanded, decisions, rounds, stop_reason = qss.run_condition(
        space,
        seed_indices,
        "recursive_unconstrained",
        options["tau_fit"],
        options["tau_gain"],
        options["max_rounds"],
        options["additions_per_round"],
        max_total_additions=options["exploration_k"],
    )
    return {
        "fraction": fraction,
        "bag": bag,
        "seed_indices": seed_indices,
        "fixed": fixed,
        "expanded": expanded,
        "decisions": decisions,
        "rounds": rounds,
        "stop_reason": stop_reason,
    }


def evaluate_selection(
    space: Any,
    selected: list[int],
    recovery_similarity: float,
) -> dict[str, Any]:
    assigned, recovered, recall, exact, ranks, mrr = qss.recovery_metrics(
        space, selected, recovery_similarity
    )
    represented_groups = {
        int(space.candidate_group[index])
        for index in selected
        if int(space.candidate_group[index]) >= 0
    }
    return {
        "selected": len(selected),
        "assigned_holdouts": assigned,
        "recovered_holdouts": recovered,
        "group_recall": recall,
        "frozen_group_coverage": (
            len(represented_groups) / len(space.groups) if space.groups else None
        ),
        "exact_hits": exact,
        "reachable_recall": exact / space.reachable_holdout_count
        if space.reachable_holdout_count else None,
        "reachable_positive_ranks": ranks,
        "reachable_positive_mrr": mrr,
    }


def aggregate(per_bag: list[dict[str, Any]], policy: str, reachable: int) -> dict[str, Any]:
    rows = [row for row in per_bag if row["policy"] == policy]
    exact = sum(int(row["exact_hits"]) for row in rows)
    denominator = len(rows) * reachable
    recalls = [float(row["group_recall"]) for row in rows if row["group_recall"] is not None]
    scope_coverages = [
        float(row["frozen_group_coverage"])
        for row in rows if row["frozen_group_coverage"] is not None
    ]
    selected = [int(row["selected"]) for row in rows]
    mrrs = [float(row["reachable_positive_mrr"]) for row in rows if row["reachable_positive_mrr"] is not None]
    return {
        "policy": policy,
        "bags": len(rows),
        "exact_hits": exact,
        "bag_positive_denominator": denominator,
        "bag_positive_recall": round(exact / denominator, 6) if denominator else None,
        "mean_group_recall": round(statistics.fmean(recalls), 6) if recalls else None,
        "mean_frozen_group_coverage": (
            round(statistics.fmean(scope_coverages), 6) if scope_coverages else None
        ),
        "mean_selected": round(statistics.fmean(selected), 6) if selected else None,
        "min_selected": min(selected) if selected else None,
        "max_selected": max(selected) if selected else None,
        "mean_positive_mrr": round(statistics.fmean(mrrs), 6) if mrrs else None,
    }


def gate_decision(evaluation_role: str, passed: bool) -> str:
    if evaluation_role == "held_out_test":
        return "held_out_test_passed" if passed else "held_out_test_failed_do_not_retune"
    if evaluation_role == "cross_sectional_scope_robustness":
        return (
            "cross_sectional_scope_robustness_completed"
            if passed else "cross_sectional_scope_robustness_failed_do_not_generalize"
        )
    return "freeze_for_test_years" if passed else "reject_or_redesign_on_development_only"


def resolved_group_capacities(
    groups: list[dict[str, Any]], override: int | None = None
) -> dict[str, int]:
    if override is not None and override < 1:
        raise ValueError("group capacity override must be positive")
    return {
        str(group["id"]): override if override is not None else int(group["capacity"])
        for group in groups
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def results_markdown(summary: dict[str, Any]) -> str:
    by_policy = {row["policy"]: row for row in summary["aggregates"]}
    gate = summary["quality_gate"]
    role_label = {
        "held_out_test": "held-out test cutoff",
        "development_only_frozen_rule": "development cutoff",
        "cross_sectional_scope_robustness": "cross-sectional scope snapshot",
    }[summary["evaluation_role"]]
    lines = [
        f"# QSS frozen late-pruner evaluation — {role_label}",
        "",
        f"Decision: **{gate['decision']}**.",
        "",
        f"Frozen rule: {summary['frozen_rule']['description']}",
        "",
        "## Results",
        "",
        "| policy | exact hits | bag-positive recall | holdout group recall | frozen-group coverage | mean selected | positive MRR |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for policy in ("fixed_top10", "recursive_first10", "late_pruned_top10"):
        row = by_policy[policy]
        bag_recall = "—" if row["bag_positive_recall"] is None else f"{row['bag_positive_recall']:.3f}"
        group_recall = "—" if row["mean_group_recall"] is None else f"{row['mean_group_recall']:.3f}"
        frozen_coverage = "—" if row["mean_frozen_group_coverage"] is None else f"{row['mean_frozen_group_coverage']:.3f}"
        lines.append(
            f"| {policy} | {row['exact_hits']} | {bag_recall} | "
            f"{group_recall} | {frozen_coverage} | {row['mean_selected']:.1f} | "
            f"{(row['mean_positive_mrr'] or 0):.3f} |"
        )
    lines += [
        "",
        "## Gate",
        "",
    ]
    lines.extend(
        f"- {'PASS' if check['passed'] else 'FAIL'} — {check['name']}: {check['detail']}"
        for check in gate["checks"]
    )
    lines += [
        "",
        "## Interpretation boundary",
        "",
        (
            f"This is a cross-sectional scope/convergence robustness analysis at bibliography snapshot year {summary['cutoff']}, with {summary['bags']} stratified bags and no temporal positive holdout. Frozen-group coverage is descriptive scope representation, not recall, accuracy, or evidence that an exact paper should have been selected. It made zero OpenAlex requests during evaluation. Unselected candidates remain unlabeled rather than negatives."
            if summary["evaluation_role"] == "cross_sectional_scope_robustness"
            else f"This is cutoff {summary['cutoff']} only, with {summary['bags']} stratified bags and {summary['reachable_holdout_count']} reachable positives. It made zero OpenAlex requests. Unselected candidates remain unlabeled rather than negatives. Current citations and 2025 Q/H-index were excluded because they leak post-cutoff information."
        ),
        "",
        "Detailed evidence: `summary.json`, `per_bag_results.csv`, and `late_pruning_audit.jsonl`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bib", required=True, type=Path)
    parser.add_argument("--candidate-pool", required=True, type=Path)
    parser.add_argument("--cutoff", type=int, required=True)
    parser.add_argument("--study", default=Path("config/qss_recursive_curation_study.json"), type=Path)
    parser.add_argument("--fractions", default="0.5,0.7,0.85")
    parser.add_argument("--bags", type=int, default=50)
    parser.add_argument("--workers", type=int, default=min(16, os.cpu_count() or 1))
    parser.add_argument("--random-seed", type=int, default=20260827)
    parser.add_argument("--evidence-threshold", type=float, default=0.75)
    parser.add_argument("--recovery-similarity", type=float, default=0.10)
    parser.add_argument("--tau-fit", type=float, default=0.18)
    parser.add_argument("--group-semantic-weight", type=float, default=0.70)
    parser.add_argument("--group-capacity", type=int)
    parser.add_argument("--tau-gain", type=float, default=0.05)
    parser.add_argument("--additions-per-round", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--exploration-k", type=int, default=30)
    parser.add_argument("--final-k", type=int, default=10)
    parser.add_argument(
        "--evaluation-role",
        choices=(
            "development_only_frozen_rule",
            "held_out_test",
            "cross_sectional_scope_robustness",
        ),
        default="development_only_frozen_rule",
    )
    parser.add_argument(
        "--rule-version",
        choices=("v1_equal_rank_hard_capacity", "v2_relevance_soft_capacity"),
        default="v2_relevance_soft_capacity",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--overwrite-output", action="store_true")
    parser.add_argument(
        "--omit-audit",
        action="store_true",
        help="Skip the large candidate-level JSONL audit for secondary sensitivity runs.",
    )
    args = parser.parse_args()
    if min(args.bags, args.workers, args.exploration_k, args.final_k) < 1:
        raise SystemExit("bags, workers, exploration-k, and final-k must be positive")
    if args.exploration_k < args.final_k:
        raise SystemExit("exploration-k must be at least final-k")
    if not 0.0 <= args.group_semantic_weight <= 1.0:
        raise SystemExit("group-semantic-weight must be between 0 and 1")
    if args.group_capacity is not None and args.group_capacity < 1:
        raise SystemExit("group-capacity must be positive")
    protected = args.output_dir / "summary.json"
    if protected.exists() and not args.overwrite_output:
        raise SystemExit(f"Refusing to overwrite {args.output_dir}; pass --overwrite-output")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    study = json.loads(args.study.read_text(encoding="utf-8"))
    groups = study["kbs_frozen_evidence_groups"]
    group_capacities = resolved_group_capacities(groups, args.group_capacity)
    payload = json.loads(args.candidate_pool.read_text(encoding="utf-8"))
    if payload.get("cutoff") not in (None, args.cutoff):
        raise SystemExit("Candidate pool cutoff does not match requested cutoff")
    candidates = payload.get("candidate_records", payload.get("recommendations", []))
    entries = qss.load_bib(args.bib)
    space = qss.build_space(
        args.cutoff,
        entries,
        candidates,
        groups,
        args.evidence_threshold,
        group_semantic_weight=args.group_semantic_weight,
    )
    candidate_scope_eligible = [
        index
        for index, group_index in enumerate(space.candidate_group)
        if int(group_index) >= 0
        and float(space.candidate_fit[index, int(group_index)]) >= args.tau_fit
    ]
    eligible_group_counts = Counter(
        str(groups[int(space.candidate_group[index])]["id"])
        for index in candidate_scope_eligible
    )
    fractions = [float(value) for value in args.fractions.split(",") if value.strip()]
    tasks = [(fraction, bag) for fraction in fractions for bag in range(args.bags)]
    options = {
        "random_seed": args.random_seed,
        "tau_fit": args.tau_fit,
        "tau_gain": args.tau_gain,
        "max_rounds": args.max_rounds,
        "additions_per_round": args.additions_per_round,
        "exploration_k": args.exploration_k,
        "final_k": args.final_k,
    }
    trajectories: list[dict[str, Any]] = []
    if args.workers == 1:
        initialize_worker(space, options)
        for completed, task in enumerate(tasks, start=1):
            trajectories.append(exploration_task(task))
            progress(completed, len(tasks), "Exploring candidates")
    else:
        with ProcessPoolExecutor(
            max_workers=args.workers,
            initializer=initialize_worker,
            initargs=(space, options),
        ) as executor:
            futures = [executor.submit(exploration_task, task) for task in tasks]
            for completed, future in enumerate(as_completed(futures), start=1):
                trajectories.append(future.result())
                progress(completed, len(futures), "Exploring candidates")
    trajectories.sort(key=lambda row: (row["fraction"], row["bag"]))

    counts_by_fraction: dict[float, Counter[int]] = defaultdict(Counter)
    for row in trajectories:
        counts_by_fraction[float(row["fraction"])].update(map(int, row["expanded"]))

    per_bag: list[dict[str, Any]] = []
    pruning_audit: list[dict[str, Any]] = []
    for completed, row in enumerate(trajectories, start=1):
        fraction = float(row["fraction"])
        prune_function = (
            late_prune_relevance_soft_capacity
            if args.rule_version == "v2_relevance_soft_capacity"
            else late_prune
        )
        late, decisions = prune_function(
            row["decisions"], counts_by_fraction[fraction], args.bags,
            group_capacities, args.tau_fit, args.final_k,
        )
        policies = {
            "fixed_top10": row["fixed"],
            "recursive_first10": row["expanded"][: args.final_k],
            "late_pruned_top10": late,
        }
        for policy, selected in policies.items():
            metrics = evaluate_selection(space, selected, args.recovery_similarity)
            per_bag.append({
                "cutoff": args.cutoff,
                "fraction": fraction,
                "bag": int(row["bag"]),
                "policy": policy,
                **{key: value for key, value in metrics.items()
                   if key not in ("reachable_positive_ranks",)},
                "reachable_positive_ranks": ";".join(
                    map(str, metrics["reachable_positive_ranks"])
                ),
            })
        pruning_audit.extend({
            "cutoff": args.cutoff,
            "fraction": fraction,
            "bag": int(row["bag"]),
            **decision,
        } for decision in decisions)
        if completed == len(trajectories) or completed % 5 == 0:
            progress(completed, len(trajectories), "Applying late pruning")

    aggregates = [
        aggregate(per_bag, policy, space.reachable_holdout_count)
        for policy in ("fixed_top10", "recursive_first10", "late_pruned_top10")
    ]
    by_policy = {row["policy"]: row for row in aggregates}
    fixed, late = by_policy["fixed_top10"], by_policy["late_pruned_top10"]
    if args.evaluation_role == "cross_sectional_scope_robustness":
        checks = [
            {
                "name": "frozen-group coverage non-inferiority",
                "passed": (
                    late["mean_frozen_group_coverage"]
                    >= fixed["mean_frozen_group_coverage"]
                ),
                "detail": (
                    f"late {late['mean_frozen_group_coverage']:.3f} versus "
                    f"fixed {fixed['mean_frozen_group_coverage']:.3f}"
                ),
            },
            {
                "name": "complete final budget",
                "passed": late["min_selected"] == late["max_selected"] == args.final_k,
                "detail": f"selected range {late['min_selected']}–{late['max_selected']}; required {args.final_k}",
            },
        ]
    else:
        checks = [
            {
                "name": "exact recovery non-inferiority",
                "passed": late["exact_hits"] >= fixed["exact_hits"],
                "detail": f"late {late['exact_hits']} versus fixed {fixed['exact_hits']} bag-positive hits",
            },
            {
                "name": "group recall non-inferiority",
                "passed": late["mean_group_recall"] >= fixed["mean_group_recall"],
                "detail": f"late {late['mean_group_recall']:.3f} versus fixed {fixed['mean_group_recall']:.3f}",
            },
            {
                "name": "complete final budget",
                "passed": late["min_selected"] == late["max_selected"] == args.final_k,
                "detail": f"selected range {late['min_selected']}–{late['max_selected']}; required {args.final_k}",
            },
        ]
    gate_passed = all(check["passed"] for check in checks)
    quality_gate = {
        "passed": gate_passed,
        "decision": gate_decision(args.evaluation_role, gate_passed),
        "checks": checks,
    }
    summary = {
        "schema_version": 1,
        "evaluation_role": args.evaluation_role,
        "cutoff": args.cutoff,
        "fractions": fractions,
        "bags": len(tasks),
        "workers": args.workers,
        "candidate_pool": len(candidates),
        "candidate_with_abstract_count": int(space.candidate_has_abstract.sum()),
        "candidate_scope_eligible_count": len(candidate_scope_eligible),
        "candidate_scope_ineligible_count": len(candidates) - len(candidate_scope_eligible),
        "candidate_scope_eligible_rate": (
            round(len(candidate_scope_eligible) / len(candidates), 6)
            if candidates else None
        ),
        "candidate_scope_eligible_group_counts": {
            str(group["id"]): eligible_group_counts.get(str(group["id"]), 0)
            for group in groups
        },
        "candidate_scope_reachable_group_count": sum(
            eligible_group_counts.get(str(group["id"]), 0) > 0 for group in groups
        ),
        "holdout_count": len(space.future),
        "reachable_holdout_count": space.reachable_holdout_count,
        "candidate_pool_ceiling": round(space.candidate_pool_ceiling, 6),
        "openalex_requests": 0,
        "frozen_rule": {
            "version": args.rule_version,
            "exploration_k": args.exploration_k,
            "final_k": args.final_k,
            "components": ["relevance", "concept_novelty_gain", "leave_one_out_bagging_confidence"],
            "component_combination": (
                "relevance_primary_with_novelty_and_confidence_tie_breaks; capacity_first_pass_then_relevance_backfill"
                if args.rule_version == "v2_relevance_soft_capacity"
                else "equal_weight_mean_of_within_bag_percentile_scores_with_hard_capacity"
            ),
            "description": (
                "Explore 30; require frozen scope fit; rank primarily by title/abstract relevance with concept novelty gain and leave-one-bag-out confidence as tie-breakers; prefer at most three papers per group in the first pass, then backfill by relevance to return 10."
                if args.rule_version == "v2_relevance_soft_capacity"
                else "Explore 30; combine equal percentile ranks of relevance, concept novelty gain, and leave-one-bag-out confidence; require frozen scope fit and hard capacity three; return up to 10."
            ),
            "tau_fit": args.tau_fit,
            "recovery_similarity": args.recovery_similarity,
            "group_semantic_weight": args.group_semantic_weight,
            "group_lexical_weight": 1.0 - args.group_semantic_weight,
            "group_capacities": group_capacities,
            "label_usage": "none in scoring or threshold selection",
        },
        "aggregates": aggregates,
        "quality_gate": quality_gate,
    }
    manifest = {
        "schema_version": 1,
        "bib": str(args.bib.resolve()),
        "candidate_pool": str(args.candidate_pool.resolve()),
        "study": str(args.study.resolve()),
        "parameters": vars(args) | {"output_dir": str(args.output_dir)},
        "cache_only": True,
        "openalex_requests": 0,
    }
    (args.output_dir / "REQUEST_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(args.output_dir / "per_bag_results.csv", per_bag)
    if not args.omit_audit:
        with (args.output_dir / "late_pruning_audit.jsonl").open("w", encoding="utf-8") as handle:
            for row in pruning_audit:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (args.output_dir / "RESULTS.md").write_text(
        results_markdown(summary), encoding="utf-8"
    )
    print(
        f"Wrote {args.output_dir}; decision={quality_gate['decision']}; "
        "OpenAlex requests=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
