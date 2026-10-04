#!/usr/bin/env python3
"""Derive frozen-pool reachability, Oracle@K, and pruning-stage diagnostics.

This script is deliberately offline. It rebuilds the exact TF-IDF/group space
used by the frozen evaluation, then derives diagnostics from the saved
candidate pools and late-pruning audit records. It never changes a frozen
policy, evidence group, candidate pool, or evaluation result.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix
from sklearn.metrics.pairwise import cosine_similarity


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import run_qss_recursive_experiment as qss  # noqa: E402


@dataclass(frozen=True)
class CasePaths:
    corpus: str
    cutoff: int
    bib: Path
    study: Path
    candidate_pool: Path
    evaluation: Path


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def case_paths(registry: dict[str, Any]) -> list[CasePaths]:
    rows: list[CasePaths] = []
    for corpus, entry in registry.get("corpora", {}).items():
        validation = entry.get("qss_validation") or {}
        if corpus == "madlab_template" or not validation:
            continue
        bib = resolve_path(validation.get("evaluation_bib", entry.get("bib", "")))
        study = resolve_path(validation.get("study_config", ""))
        for cutoff_value in validation.get("cutoffs", []):
            cutoff = int(cutoff_value)
            candidate_value = validation.get("candidate_pools", {}).get(str(cutoff))
            candidate_pool = (
                resolve_path(candidate_value)
                if candidate_value
                else PROJECT_ROOT / "outputs" / "qss-multicorpus" / corpus
                / "candidates" / f"qss_candidates_{cutoff}.json"
            )
            evaluation_value = validation.get("evaluation_outputs", {}).get(str(cutoff))
            evaluation = (
                resolve_path(evaluation_value)
                if evaluation_value
                else PROJECT_ROOT / "outputs" / "qss-multicorpus" / corpus
                / "evaluation" / str(cutoff)
            )
            rows.append(CasePaths(
                corpus=corpus,
                cutoff=cutoff,
                bib=bib,
                study=study,
                candidate_pool=candidate_pool,
                evaluation=evaluation,
            ))
    return rows


def validate_case_files(case: CasePaths) -> None:
    required = [
        case.bib,
        case.study,
        case.candidate_pool,
        case.evaluation / "REQUEST_MANIFEST.json",
        case.evaluation / "summary.json",
        case.evaluation / "late_pruning_audit.jsonl",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"{case.corpus}-{case.cutoff} is missing required frozen artifacts: "
            + ", ".join(missing)
        )


def unique_coverage_patterns(
    incidence: np.ndarray,
    candidate_indices: list[int],
    candidate_fit: np.ndarray,
    candidate_group: np.ndarray,
) -> tuple[np.ndarray, list[int]]:
    """Compress candidates with identical holdout coverage deterministically."""
    best: dict[bytes, int] = {}
    for column, candidate_index in enumerate(candidate_indices):
        pattern = np.packbits(incidence[:, column]).tobytes()
        if not incidence[:, column].any():
            continue
        previous = best.get(pattern)
        group = int(candidate_group[candidate_index])
        score = float(candidate_fit[candidate_index, group])
        if previous is None:
            best[pattern] = candidate_index
            continue
        previous_group = int(candidate_group[previous])
        previous_score = float(candidate_fit[previous, previous_group])
        if (score, -candidate_index) > (previous_score, -previous):
            best[pattern] = candidate_index
    representatives = sorted(best.values())
    if not representatives:
        return np.zeros((incidence.shape[0], 0), dtype=bool), []
    original_positions = {candidate: pos for pos, candidate in enumerate(candidate_indices)}
    compressed = np.column_stack([
        incidence[:, original_positions[candidate]] for candidate in representatives
    ])
    return compressed, representatives


def maximum_coverage_oracle(
    incidence: np.ndarray,
    candidate_indices: list[int],
    candidate_fit: np.ndarray,
    candidate_group: np.ndarray,
    budget: int,
) -> tuple[int, list[int]]:
    """Solve exact maximum holdout coverage under a fixed candidate budget."""
    holdouts = incidence.shape[0]
    if holdouts == 0 or not candidate_indices or budget <= 0:
        return 0, []
    compressed, representatives = unique_coverage_patterns(
        incidence, candidate_indices, candidate_fit, candidate_group
    )
    if not representatives:
        return 0, []
    papers = len(representatives)
    variables = papers + holdouts
    objective = np.concatenate([
        np.full(papers, 1e-5, dtype=float),
        np.full(holdouts, -1.0, dtype=float),
    ])
    matrix = lil_matrix((holdouts + 1, variables), dtype=float)
    matrix[0, :papers] = 1.0
    for holdout in range(holdouts):
        covering = np.flatnonzero(compressed[holdout])
        matrix[holdout + 1, covering] = -1.0
        matrix[holdout + 1, papers + holdout] = 1.0
    lower = np.full(holdouts + 1, -np.inf, dtype=float)
    upper = np.zeros(holdouts + 1, dtype=float)
    upper[0] = budget
    result = milp(
        c=objective,
        integrality=np.ones(variables, dtype=int),
        bounds=Bounds(np.zeros(variables), np.ones(variables)),
        constraints=LinearConstraint(matrix.tocsr(), lower, upper),
        options={"presolve": True},
    )
    if not result.success or result.x is None:
        raise RuntimeError(f"Oracle MILP failed: {result.message}")
    selected = [
        representatives[index]
        for index, value in enumerate(result.x[:papers])
        if value > 0.5
    ]
    recovered = int(round(float(result.x[papers:].sum())))
    return recovered, selected


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def replay_fixed_selections(
    space: Any, parameters: dict[str, Any]
) -> dict[tuple[float, int], set[int]]:
    """Replay the deterministic fixed baseline from frozen manifest settings."""
    result: dict[tuple[float, int], set[int]] = {}
    fractions = [
        float(value)
        for value in str(parameters.get("fractions", "0.5,0.7,0.85")).split(",")
        if value.strip()
    ]
    bags = int(parameters.get("bags", 50))
    random_seed = int(parameters.get("random_seed", 20260827))
    tau_fit = float(parameters.get("tau_fit", 0.18))
    tau_gain = float(parameters.get("tau_gain", 0.05))
    max_rounds = int(parameters.get("max_rounds", 3))
    final_k = int(parameters.get("final_k", 10))
    for fraction in fractions:
        for bag in range(bags):
            rng = random.Random(
                random_seed
                + space.cutoff * 10000
                + int(fraction * 1000) * 100
                + bag
            )
            seed_indices = qss.stratified_sample_indices(space.past, fraction, rng)
            selected, _, _, _ = qss.run_condition(
                space,
                seed_indices,
                "fixed",
                tau_fit,
                tau_gain,
                max_rounds,
                final_k,
                max_total_additions=final_k,
            )
            result[(fraction, bag)] = set(map(int, selected))
    return result


def saved_policy_selections(
    audit: list[dict[str, Any]], fixed: dict[tuple[float, int], set[int]], final_k: int
) -> dict[str, dict[tuple[float, int], set[int]]]:
    """Recover saved recursive/late sets and combine them with fixed replay."""
    by_bag: dict[tuple[float, int], list[dict[str, Any]]] = defaultdict(list)
    for row in audit:
        by_bag[(float(row["fraction"]), int(row["bag"]))].append(row)
    recursive: dict[tuple[float, int], set[int]] = {}
    late: dict[tuple[float, int], set[int]] = {}
    for bag, rows in by_bag.items():
        ordered = sorted(
            rows,
            key=lambda row: (
                int(row.get("round", 10**9)),
                int(row.get("rank_within_round", 10**9)),
                int(row["candidate_index"]),
            ),
        )
        recursive[bag] = {
            int(row["candidate_index"]) for row in ordered[:final_k]
        }
        late[bag] = {
            int(row["candidate_index"])
            for row in rows
            if row.get("final_decision") == "selected"
        }
    if set(fixed) != set(by_bag):
        raise ValueError("Fixed replay and saved audit contain different bag keys")
    return {
        "fixed_top10": fixed,
        "recursive_first10": recursive,
        "late_pruned_top10": late,
    }


def pairwise_jaccard_rows(
    selections: dict[str, dict[tuple[float, int], set[int]]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for policy, by_bag in selections.items():
        fractions = sorted({fraction for fraction, _ in by_bag})
        for fraction in fractions:
            sets = [
                selected for (value, _), selected in sorted(by_bag.items())
                if value == fraction
            ]
            values = []
            for left in range(len(sets)):
                for right in range(left + 1, len(sets)):
                    union = sets[left] | sets[right]
                    values.append(len(sets[left] & sets[right]) / len(union) if union else 1.0)
            rows.append({
                "policy": policy,
                "fraction": fraction,
                "bag_pairs": len(values),
                "mean_pairwise_jaccard": round(statistics.fmean(values), 6) if values else None,
                "median_pairwise_jaccard": round(statistics.median(values), 6) if values else None,
                "min_pairwise_jaccard": round(min(values), 6) if values else None,
                "max_pairwise_jaccard": round(max(values), 6) if values else None,
            })
    return rows


def exact_positive_frequency_rows(
    space: Any,
    selections: dict[str, dict[tuple[float, int], set[int]]],
) -> list[dict[str, Any]]:
    candidate_titles, candidate_dois = qss.identity_keys(space.candidates)
    rows: list[dict[str, Any]] = []
    for future_index, holdout in enumerate(space.future):
        if not qss.same_work(holdout, candidate_titles, candidate_dois):
            continue
        holdout_titles, holdout_dois = qss.identity_keys([holdout])
        matching = {
            index for index, candidate in enumerate(space.candidates)
            if qss.same_work(candidate, holdout_titles, holdout_dois)
        }
        for policy, by_bag in selections.items():
            selected_bags = sum(bool(matching & selected) for selected in by_bag.values())
            rows.append({
                "policy": policy,
                "future_index": future_index,
                "title": holdout.get("title", ""),
                "year": qss.publication_year(holdout),
                "doi": holdout.get("doi", ""),
                "matching_candidate_indices": ";".join(map(str, sorted(matching))),
                "selected_bags": selected_bags,
                "total_bags": len(by_bag),
                "selection_frequency": round(selected_bags / len(by_bag), 6)
                if by_bag else None,
            })
    return rows


def concentration(values: list[str]) -> dict[str, Any]:
    counts = Counter(values)
    total = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return {
        "observations": total,
        "unique_origin_signatures": len(counts),
        "top_origin_signature": ordered[0][0] if ordered else None,
        "top_origin_count": ordered[0][1] if ordered else 0,
        "top_origin_share": round(ordered[0][1] / total, 6) if total else None,
        "hhi": round(sum((count / total) ** 2 for count in counts.values()), 6)
        if total else None,
        "origin_counts": dict(ordered),
    }


def origin_concentration_rows(
    space: Any,
    selections: dict[str, dict[tuple[float, int], set[int]]],
) -> tuple[float, list[dict[str, Any]]]:
    origins = [str(row.get("discovery_channels", "")).strip() for row in space.candidates]
    coverage = sum(bool(value) for value in origins) / len(origins) if origins else 0.0
    rows = [{"population": "candidate_pool", **concentration([v for v in origins if v])}]
    if coverage == 1.0:
        for policy, by_bag in selections.items():
            occurrences = [origins[index] for selected in by_bag.values() for index in selected]
            rows.append({"population": f"selection_occurrences:{policy}", **concentration(occurrences)})
    return round(coverage, 6), rows


def stage_counts(
    audit: list[dict[str, Any]], group_ids: list[str]
) -> tuple[int, dict[str, dict[str, int]]]:
    bags = sorted({(float(row["fraction"]), int(row["bag"])) for row in audit})
    by_bag: dict[tuple[float, int], list[dict[str, Any]]] = defaultdict(list)
    for row in audit:
        by_bag[(float(row["fraction"]), int(row["bag"]))].append(row)
    result: dict[str, dict[str, int]] = {}
    for group in group_ids:
        explored = scope_eligible = final = 0
        for bag in bags:
            rows = [row for row in by_bag[bag] if row.get("group") == group]
            has_explored = bool(rows)
            has_scope = any(bool(row.get("passes_final_scope_gate")) for row in rows)
            has_final = any(row.get("final_decision") == "selected" for row in rows)
            explored += has_explored
            scope_eligible += has_scope
            final += has_final
        result[group] = {
            "explored_bags": explored,
            "scope_eligible_explored_bags": scope_eligible,
            "final_represented_bags": final,
            "exploration_omission_bags": len(bags) - scope_eligible,
            "pruning_omission_bags": scope_eligible - final,
            "final_omission_bags": len(bags) - final,
        }
    return len(bags), result


def candidate_summary(space: Any, index: int) -> dict[str, Any]:
    row = space.candidates[index]
    group_index = int(space.candidate_group[index])
    return {
        "candidate_index": index,
        "openalex_id": row.get("openalex_id", ""),
        "doi": row.get("doi", ""),
        "title": row.get("title", ""),
        "year": qss.publication_year(row),
        "group": space.groups[group_index]["id"] if group_index >= 0 else None,
        "group_fit": round(float(space.candidate_fit[index, group_index]), 6)
        if group_index >= 0 else None,
    }


BOUNDARY_TITLE_PATTERNS = {
    "C1": (
        "Designing an Interactive Visual Analytics System for Precipitation Data Analysis",
    ),
    "C2": ("TCWV prediction",),
    "C3": ("REFCON:",),
    "C5": (
        "Fine-Grained Soybean Variety Recognition",
        "RGB-T Tracking",
        "pig tracking",
    ),
    "C4": ("AI Voice Agent",),
}


def review_candidate_row(
    case: CasePaths,
    space: Any,
    index: int,
    audit_role: str,
    target_group: str,
    evidence: str,
    audit_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate = space.candidates[index]
    group_index = int(space.candidate_group[index])
    occurrences = [row for row in audit_rows if int(row["candidate_index"]) == index]
    selected_occurrences = [row for row in occurrences if row.get("final_decision") == "selected"]
    relevance = [float(row["relevance"]) for row in occurrences]
    utility = [float(row["utility"]) for row in occurrences]
    return {
        "corpus": case.corpus,
        "cutoff": case.cutoff,
        "audit_role": audit_role,
        "target_group": target_group,
        "candidate_index": index,
        "openalex_id": candidate.get("openalex_id", ""),
        "doi": candidate.get("doi", ""),
        "title": candidate.get("title", ""),
        "year": qss.publication_year(candidate),
        "abstract": candidate.get("abstract", ""),
        "machine_group": space.groups[group_index]["id"] if group_index >= 0 else None,
        "group_fit": round(float(space.candidate_fit[index, group_index]), 6)
        if group_index >= 0 else None,
        "audit_occurrences": len(occurrences),
        "final_selected_occurrences": len(selected_occurrences),
        "mean_relevance_when_explored": round(float(np.mean(relevance)), 6)
        if relevance else None,
        "mean_utility_when_explored": round(float(np.mean(utility)), 6)
        if utility else None,
        "sampling_evidence": evidence,
        "human_scope_decision": "",
        "human_group_decision": "",
        "human_reason_code": "",
        "human_notes": "",
    }


def targeted_human_audit_rows(
    case: CasePaths,
    space: Any,
    audit: list[dict[str, Any]],
    group_rows: list[dict[str, Any]],
    eligible: list[int],
) -> list[dict[str, Any]]:
    """Build a targeted, explicitly non-random construct-audit queue."""
    rows: list[dict[str, Any]] = []
    audit_by_bag: dict[tuple[float, int], list[dict[str, Any]]] = defaultdict(list)
    for row in audit:
        audit_by_bag[(float(row["fraction"]), int(row["bag"]))].append(row)
    for group in group_rows:
        bags = int(group["final_represented_bags"] + group["final_omission_bags"])
        omission_rate = group["final_omission_bags"] / bags if bags else 0.0
        if omission_rate < 0.10:
            continue
        group_id = str(group["group"])
        group_index = next(
            index for index, value in enumerate(space.groups)
            if str(value["id"]) == group_id
        )
        explored = [
            row for row in audit
            if row.get("group") == group_id and row.get("passes_final_scope_gate")
        ]
        if explored:
            counts = Counter(int(row["candidate_index"]) for row in explored)
            representative = min(
                (index for index, count in counts.items() if count == max(counts.values())),
                key=lambda index: qss.title_key(space.candidates[index]),
            )
            evidence = (
                f"Most frequently explored scope-eligible candidate for a group omitted "
                f"from {group['final_omission_bags']}/{bags} final bags."
            )
        else:
            pool = [
                index for index in eligible
                if int(space.candidate_group[index]) == group_index
            ]
            representative = max(
                pool,
                key=lambda index: (
                    float(space.candidate_fit[index, group_index]),
                    -index,
                ),
            )
            evidence = (
                f"Highest-fit pool candidate for a reachable group that never entered "
                f"the scope-eligible explored set in {bags} bags."
            )
        rows.append(review_candidate_row(
            case, space, representative, "omitted_group_representative",
            group_id, evidence, audit,
        ))

        omitted_bags = [
            bag for bag, bag_rows in audit_by_bag.items()
            if not any(
                row.get("group") == group_id and row.get("final_decision") == "selected"
                for row in bag_rows
            )
        ]
        competing = Counter(
            int(row["candidate_index"])
            for bag in omitted_bags for row in audit_by_bag[bag]
            if row.get("final_decision") == "selected" and row.get("group") != group_id
        )
        if competing:
            competitor = min(
                (index for index, count in competing.items() if count == max(competing.values())),
                key=lambda index: qss.title_key(space.candidates[index]),
            )
            rows.append(review_candidate_row(
                case, space, competitor, "frequent_selected_context",
                group_id,
                f"Most frequently selected non-{group_id} candidate in bags where the target group was absent; contextual, not a one-to-one causal replacement.",
                audit,
            ))

    normalized_existing = {
        (row["corpus"], qss.normalize(row["title"])) for row in rows
    }
    boundary_patterns = BOUNDARY_TITLE_PATTERNS.get(case.corpus, ())
    for index, candidate in enumerate(space.candidates):
        title = str(candidate.get("title", ""))
        if not any(pattern.lower() in title.lower() for pattern in boundary_patterns):
            continue
        key = (case.corpus, qss.normalize(title))
        if key in normalized_existing:
            continue
        rows.append(review_candidate_row(
            case, space, index, "known_boundary_trace",
            str(space.groups[int(space.candidate_group[index])]["id"])
            if int(space.candidate_group[index]) >= 0 else "unassigned",
            "Named boundary example already used in the case-level diagnostic narrative.",
            audit,
        ))
        normalized_existing.add(key)
    return rows


def derive_case(case: CasePaths) -> dict[str, Any]:
    validate_case_files(case)
    manifest = json.loads(
        (case.evaluation / "REQUEST_MANIFEST.json").read_text(encoding="utf-8")
    )
    summary = json.loads((case.evaluation / "summary.json").read_text(encoding="utf-8"))
    parameters = manifest.get("parameters", {})
    evidence_threshold = float(parameters.get("evidence_threshold", 0.75))
    recovery_similarity = float(parameters.get("recovery_similarity", 0.10))
    tau_fit = float(parameters.get("tau_fit", 0.18))
    final_k = int(parameters.get("final_k", 10))
    study = json.loads(case.study.read_text(encoding="utf-8"))
    groups = study["kbs_frozen_evidence_groups"]
    candidate_payload = json.loads(case.candidate_pool.read_text(encoding="utf-8"))
    candidates = candidate_payload.get("candidate_records", [])
    space = qss.build_space(
        case.cutoff,
        qss.load_bib(case.bib),
        candidates,
        groups,
        evidence_threshold,
    )
    eligible = [
        index for index in range(len(space.candidates))
        if space.candidate_has_abstract[index]
        and int(space.candidate_group[index]) >= 0
        and float(space.candidate_fit[index, int(space.candidate_group[index])]) >= tau_fit
    ]
    eligible_by_group: Counter[int] = Counter(int(space.candidate_group[index]) for index in eligible)
    reachable_groups = [index for index in range(len(groups)) if eligible_by_group[index] > 0]
    oracle_group_count = min(len(reachable_groups), final_k)

    assigned_future = [
        index for index, group in enumerate(space.future_group) if int(group) >= 0
    ]
    incidence = np.zeros((len(assigned_future), len(eligible)), dtype=bool)
    for row_index, future_index in enumerate(assigned_future):
        group = int(space.future_group[future_index])
        comparable_positions = [
            position for position, candidate_index in enumerate(eligible)
            if int(space.candidate_group[candidate_index]) == group
        ]
        if comparable_positions:
            candidate_indices = [eligible[position] for position in comparable_positions]
            similarities = cosine_similarity(
                space.future_matrix[future_index], space.candidate_matrix[candidate_indices]
            ).ravel()
            for position, similarity in zip(comparable_positions, similarities):
                incidence[row_index, position] = float(similarity) >= recovery_similarity
    role_reachable = int(incidence.any(axis=1).sum()) if len(assigned_future) else 0
    oracle_role_recovered, oracle_selected = maximum_coverage_oracle(
        incidence,
        eligible,
        space.candidate_fit,
        space.candidate_group,
        final_k,
    )

    audit = load_jsonl(case.evaluation / "late_pruning_audit.jsonl")
    fixed_selections = replay_fixed_selections(space, parameters)
    selections = saved_policy_selections(audit, fixed_selections, final_k)
    stability = pairwise_jaccard_rows(selections)
    positive_frequencies = exact_positive_frequency_rows(space, selections)
    origin_coverage, origin_concentrations = origin_concentration_rows(space, selections)
    reported_exact = {
        str(row["policy"]): int(row["exact_hits"])
        for row in summary.get("aggregates", [])
    }
    derived_exact = Counter()
    for row in positive_frequencies:
        derived_exact[str(row["policy"])] += int(row["selected_bags"])
    mismatches = {
        policy: (derived_exact[policy], exact_hits)
        for policy, exact_hits in reported_exact.items()
        if derived_exact[policy] != exact_hits
    }
    if mismatches:
        raise ValueError(
            f"{case.corpus}-{case.cutoff} exact-frequency reconciliation failed: "
            f"{mismatches}"
        )
    bags, group_stages = stage_counts(audit, [str(group["id"]) for group in groups])
    group_rows = []
    for group_index, group in enumerate(groups):
        group_id = str(group["id"])
        counts = group_stages[group_id]
        group_rows.append({
            "group": group_id,
            "pool_eligible_candidates": eligible_by_group[group_index],
            "pool_reachable": eligible_by_group[group_index] > 0,
            **counts,
        })
    human_audit_rows = targeted_human_audit_rows(
        case, space, audit, group_rows, eligible
    )
    return {
        "schema_version": 2,
        "corpus": case.corpus,
        "cutoff": case.cutoff,
        "evaluation_role": summary.get("evaluation_role"),
        "parameters": {
            "evidence_threshold": evidence_threshold,
            "recovery_similarity": recovery_similarity,
            "tau_fit": tau_fit,
            "final_k": final_k,
        },
        "artifact_paths": {
            "bib": str(case.bib),
            "study": str(case.study),
            "candidate_pool": str(case.candidate_pool),
            "evaluation": str(case.evaluation),
        },
        "candidate_count": len(space.candidates),
        "eligible_candidate_count": len(eligible),
        "future_holdout_count": len(space.future),
        "exact_reachable_holdout_count": space.reachable_holdout_count,
        "exact_retrieval_ceiling": round(space.candidate_pool_ceiling, 6)
        if space.future else None,
        "frozen_group_count": len(groups),
        "reachable_frozen_group_count": len(reachable_groups),
        "frozen_group_reachability": round(len(reachable_groups) / len(groups), 6)
        if groups else None,
        "oracle_frozen_group_coverage_at_k": round(oracle_group_count / len(groups), 6)
        if groups else None,
        "assigned_holdout_count": len(assigned_future),
        "holdout_role_reachable_count": role_reachable,
        "holdout_role_reachability_ceiling": round(role_reachable / len(assigned_future), 6)
        if assigned_future else None,
        "oracle_holdout_role_recovered_at_k": oracle_role_recovered
        if assigned_future else None,
        "oracle_holdout_role_recall_at_k": round(
            oracle_role_recovered / len(assigned_future), 6
        ) if assigned_future else None,
        "oracle_selected_candidates": [candidate_summary(space, index) for index in oracle_selected],
        "exact_positive_selection_frequency": positive_frequencies,
        "selection_set_stability": stability,
        "discovery_origin_metadata_coverage": origin_coverage,
        "discovery_origin_concentration": origin_concentrations,
        "bags": bags,
        "groups": group_rows,
        "human_audit_queue": human_audit_rows,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def markdown_report(cases: list[dict[str, Any]]) -> str:
    lines = [
        "# QSS Frozen-Pool Stage Diagnostics",
        "",
        "All values are derived offline from frozen candidate pools, study configs,",
        "and evaluation audits. No OpenAlex request or policy retuning is performed.",
        "",
        "## Metric Definitions",
        "",
        "- `Frozen-group reachability`: fraction of frozen groups with at least one abstract-bearing candidate assigned by the frozen anchor/group logic and passing `tau_fit`.",
        "- `Holdout-role reachability ceiling`: fraction of assigned future holdouts with at least one scope-eligible candidate in the same group meeting the frozen cosine recovery threshold.",
        "- `Oracle frozen coverage@10`: maximum nominal frozen-group presence possible with ten candidates.",
        "- `Oracle holdout-role recall@10`: exact maximum holdout-role recovery possible with ten scope-eligible candidates, solved as a binary maximum-coverage problem.",
        "- `Exploration omission`: no candidate from the group both entered explored-30 and passed the final scope gate in a bag.",
        "- `Pruning omission`: a scope-eligible group candidate entered explored-30, but none from that group survived final Top-10.",
        "- `Exact retrieval ceiling`: fraction of future holdouts whose title or DOI occurs in the frozen candidate pool; ranking cannot recover an identity outside this pool.",
        "- `Selection frequency`: fraction of the 150 saved/replayed bags in which a reachable exact holdout was selected by a policy.",
        "- `Pairwise Jaccard`: overlap divided by union for Top-10 selection sets, compared only among bags with the same seed fraction.",
        "- `Origin concentration`: HHI and largest share over exact `discovery_channels` signatures. Signatures are kept intact rather than fractionally splitting multi-channel records.",
        "",
        "## Case Summary",
        "",
        "| Corpus | Cutoff | Frozen reachability | Oracle frozen@10 | Holdout-role ceiling | Oracle role@10 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for case in cases:
        role_ceiling = case["holdout_role_reachability_ceiling"]
        oracle_role = case["oracle_holdout_role_recall_at_k"]
        lines.append(
            f"| {case['corpus']} | {case['cutoff']} | "
            f"{case['frozen_group_reachability']:.3f} | "
            f"{case['oracle_frozen_group_coverage_at_k']:.3f} | "
            f"{role_ceiling:.3f} | {oracle_role:.3f} |"
            if role_ceiling is not None and oracle_role is not None else
            f"| {case['corpus']} | {case['cutoff']} | "
            f"{case['frozen_group_reachability']:.3f} | "
            f"{case['oracle_frozen_group_coverage_at_k']:.3f} | N/A | N/A |"
        )
    lines += ["", "## Group Stage Localization", ""]
    for case in cases:
        lines += [
            f"### {case['corpus']} — {case['cutoff']}",
            "",
            "| Group | Pool eligible | Explored + scope-pass bags | Final bags | Exploration omissions | Pruning omissions |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for group in case["groups"]:
            lines.append(
                f"| `{group['group']}` | {group['pool_eligible_candidates']} | "
                f"{group['scope_eligible_explored_bags']} | "
                f"{group['final_represented_bags']} | "
                f"{group['exploration_omission_bags']} | "
                f"{group['pruning_omission_bags']} |"
            )
        lines.append("")
    lines += ["## Exact-Positive Selection Frequency", ""]
    for case in cases:
        rows = case["exact_positive_selection_frequency"]
        if not rows:
            continue
        lines += [
            f"### {case['corpus']} — {case['cutoff']}",
            "",
            "| Reachable exact holdout | Policy | Selected bags | Frequency |",
            "|---|---|---:|---:|",
        ]
        for row in rows:
            lines.append(
                f"| {row['title']} | `{row['policy']}` | "
                f"{row['selected_bags']}/{row['total_bags']} | "
                f"{row['selection_frequency']:.3f} |"
            )
        lines.append("")
    lines += [
        "## Selection-Set Stability",
        "",
        "Jaccard comparisons are restricted to pairs of bags at the same seed fraction; each row contains 1,225 bag pairs.",
        "",
        "| Corpus | Cutoff | Policy | Seed fraction | Mean Jaccard | Median | Range |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for case in cases:
        for row in case["selection_set_stability"]:
            lines.append(
                f"| {case['corpus']} | {case['cutoff']} | `{row['policy']}` | "
                f"{row['fraction']:.2f} | {row['mean_pairwise_jaccard']:.3f} | "
                f"{row['median_pairwise_jaccard']:.3f} | "
                f"{row['min_pairwise_jaccard']:.3f}–{row['max_pairwise_jaccard']:.3f} |"
            )
    lines += [
        "",
        "## Discovery-Origin Concentration",
        "",
        "All evaluated candidate records contain a `discovery_channels` signature. The table reports pool concentration and selection-occurrence concentration; it does not interpret a retrieval channel as causal.",
        "",
        "| Corpus | Cutoff | Population | Top origin | Share | HHI |",
        "|---|---:|---|---|---:|---:|",
    ]
    for case in cases:
        for row in case["discovery_origin_concentration"]:
            lines.append(
                f"| {case['corpus']} | {case['cutoff']} | `{row['population']}` | "
                f"`{row['top_origin_signature']}` | {row['top_origin_share']:.3f} | "
                f"{row['hhi']:.3f} |"
            )
    lines += [
        "## Interpretation Boundary",
        "",
        "These diagnostics localize opportunity and displacement under the frozen automatic assignments. They do not establish that an assigned candidate is substantively valid evidence. Human construct/boundary review remains separate and may reinterpret an omission as an annotation false positive, but it must not alter the frozen outcomes.",
        "",
    ]
    return "\n".join(lines)


def human_audit_guide(row_count: int) -> str:
    return f"""# QSS Research-Team Construct Adjudication Guide

## Purpose

Research-team domain collaborators review the targeted {row_count}-row queue in
`human_construct_audit_queue.csv` to distinguish an automatic group-assignment
error from a genuine policy blind spot. The object of analysis is the scholarly
document and system assignment, not the collaborator. This is explanatory L2
adjudication. It must not change frozen groups,
candidate pools, policies, or already reported outcomes.

The `human_*` column prefix is retained only for schema compatibility. In the
paper, describe this activity as `research-team/domain-owner adjudication`, not
as blinded independent human evaluation.

## Required Fields

- `human_scope_decision`: `KEEP_IN_SCOPE`, `REMOVE_OUT_OF_SCOPE`, or `DEFER_FULL_TEXT`.
- `human_group_decision`: `CORRECT_GROUP`, `WRONG_GROUP`, `PARTIAL_ANALOGUE`, or `UNASSIGNED`.
- `human_reason_code`: use one primary code below.
- `human_notes`: one concrete sentence tied to the research concept, method,
  population/object, outcome, or evidence role.

## Reason Codes

- `DIRECT_METHOD_OR_EVIDENCE_ROLE`
- `BRIDGE_REFERENCE`
- `WRONG_DOMAIN_OR_POPULATION`
- `WRONG_METHOD_OR_OUTCOME`
- `FALSE_LEXICAL_OR_SEMANTIC_MATCH`
- `TOO_GENERIC_FOR_CLAIM`
- `INSUFFICIENT_ABSTRACT_EVIDENCE`
- `OUTSIDE_FROZEN_BOUNDARY`
- `VALID_CONTEXT_BUT_WRONG_GROUP`

## Sampling Boundary

The queue is targeted rather than random. It contains systematic omitted-group
representatives, frequent selected context papers from omission bags, and named
boundary traces already used in the narrative. It estimates neither corpus-wide
error rate nor inter-rater agreement. A future blinded dual-review sample is
needed for those claims.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry", type=Path,
        default=PROJECT_ROOT / "config" / "qss_corpus_registry.json",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=PROJECT_ROOT / "outputs" / "qss-stage-diagnostics",
    )
    parser.add_argument("--corpus", action="append", default=[])
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    cases = case_paths(registry)
    if args.corpus:
        requested = set(args.corpus)
        cases = [case for case in cases if case.corpus in requested]
    derived = [derive_case(case) for case in cases]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "cache_only": True,
        "openalex_requests": 0,
        "cases": derived,
    }
    (args.output_dir / "stage_diagnostics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary_rows = [{
        "corpus": case["corpus"],
        "cutoff": case["cutoff"],
        "evaluation_role": case["evaluation_role"],
        "candidate_count": case["candidate_count"],
        "eligible_candidate_count": case["eligible_candidate_count"],
        "future_holdout_count": case["future_holdout_count"],
        "exact_reachable_holdout_count": case["exact_reachable_holdout_count"],
        "exact_retrieval_ceiling": case["exact_retrieval_ceiling"],
        "frozen_group_reachability": case["frozen_group_reachability"],
        "oracle_frozen_group_coverage_at_k": case["oracle_frozen_group_coverage_at_k"],
        "assigned_holdout_count": case["assigned_holdout_count"],
        "holdout_role_reachability_ceiling": case["holdout_role_reachability_ceiling"],
        "oracle_holdout_role_recall_at_k": case["oracle_holdout_role_recall_at_k"],
    } for case in derived]
    write_csv(args.output_dir / "case_summary.csv", summary_rows)
    group_rows = []
    audit_rows = []
    positive_rows = []
    stability_rows = []
    origin_rows = []
    for case in derived:
        for group in case["groups"]:
            group_rows.append({"corpus": case["corpus"], "cutoff": case["cutoff"], **group})
        audit_rows.extend(case["human_audit_queue"])
        positive_rows.extend({
            "corpus": case["corpus"], "cutoff": case["cutoff"], **row
        } for row in case["exact_positive_selection_frequency"])
        stability_rows.extend({
            "corpus": case["corpus"], "cutoff": case["cutoff"], **row
        } for row in case["selection_set_stability"])
        origin_rows.extend({
            "corpus": case["corpus"], "cutoff": case["cutoff"],
            "metadata_coverage": case["discovery_origin_metadata_coverage"],
            **{key: value for key, value in row.items() if key != "origin_counts"},
            "origin_counts_json": json.dumps(row["origin_counts"], ensure_ascii=False, sort_keys=True),
        } for row in case["discovery_origin_concentration"])
    write_csv(args.output_dir / "group_stage_localization.csv", group_rows)
    write_csv(args.output_dir / "human_construct_audit_queue.csv", audit_rows)
    write_csv(args.output_dir / "exact_positive_selection_frequency.csv", positive_rows)
    write_csv(args.output_dir / "selection_set_stability.csv", stability_rows)
    write_csv(args.output_dir / "discovery_origin_concentration.csv", origin_rows)
    (args.output_dir / "HUMAN_AUDIT_GUIDE.md").write_text(
        human_audit_guide(len(audit_rows)), encoding="utf-8"
    )
    (args.output_dir / "REPORT.md").write_text(markdown_report(derived), encoding="utf-8")
    print(f"Wrote {len(derived)} cases to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
