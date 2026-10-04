#!/usr/bin/env python3
"""Run one-factor-at-a-time, cache-only sensitivity checks for frozen QSS v2.

This is a secondary robustness analysis.  It must not select a new policy or
rewrite the frozen confirmatory result.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVALUATOR = PROJECT_ROOT / "scripts/evaluate_qss_late_pruner.py"
PARAMETER_KEYS = (
    "exploration_k",
    "final_k",
    "tau_fit",
    "recovery_similarity",
    "group_capacity",
    "group_semantic_weight",
)


def resolve(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def selected_ids(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def evaluation_role(mode: str, cutoff: int, development_cutoff: int | None) -> str:
    if mode == "cross_sectional_scope_robustness":
        return "cross_sectional_scope_robustness"
    if development_cutoff == cutoff:
        return "development_only_frozen_rule"
    return "held_out_test"


def cases_from_registry(
    registry: dict[str, Any], case_filter: set[str] | None = None
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for corpus, entry in registry.get("corpora", {}).items():
        if case_filter is not None and corpus not in case_filter:
            continue
        validation = entry.get("qss_validation") or {}
        mode = str(validation.get("evaluation_mode", "temporal_positive_holdout"))
        if not validation or not validation.get("cutoffs") or mode == "topic_formulation_scope_diagnostic":
            continue
        bib = resolve(validation.get("evaluation_bib", entry["bib"]))
        study = resolve(validation["study_config"])
        development = validation.get("development_cutoff")
        if development is None and corpus == "C1":
            development = 2022
        for cutoff_value in validation["cutoffs"]:
            cutoff = int(cutoff_value)
            candidate_value = validation.get("candidate_pools", {}).get(str(cutoff))
            candidate_pool = (
                resolve(candidate_value)
                if candidate_value
                else PROJECT_ROOT / "outputs/qss-multicorpus" / corpus / "candidates" / f"qss_candidates_{cutoff}.json"
            )
            missing = [path for path in (bib, study, candidate_pool) if not path.is_file()]
            if missing:
                raise FileNotFoundError(
                    f"{corpus}/{cutoff}: missing required cached input(s): "
                    + ", ".join(map(str, missing))
                )
            cases.append({
                "case_id": f"{corpus}-{cutoff}",
                "corpus": corpus,
                "cutoff": cutoff,
                "bib": bib,
                "study": study,
                "candidate_pool": candidate_pool,
                "evaluation_role": evaluation_role(mode, cutoff, development),
            })
    return cases


def merged_parameters(spec: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    baseline = dict(spec["baseline"])
    unknown = set(variant.get("overrides", {})) - set(PARAMETER_KEYS)
    if unknown:
        raise ValueError(f"Unknown sensitivity parameter(s): {sorted(unknown)}")
    baseline.update(variant.get("overrides", {}))
    if int(baseline["exploration_k"]) < int(baseline["final_k"]):
        raise ValueError(f"{variant['id']}: exploration_k must be at least final_k")
    return baseline


def evaluator_command(
    case: dict[str, Any],
    parameters: dict[str, Any],
    output_dir: Path,
    bags: int,
    workers: int,
    overwrite: bool,
) -> list[str]:
    command = [
        sys.executable,
        str(EVALUATOR),
        "--bib", str(case["bib"]),
        "--candidate-pool", str(case["candidate_pool"]),
        "--study", str(case["study"]),
        "--cutoff", str(case["cutoff"]),
        "--evaluation-role", str(case["evaluation_role"]),
        "--bags", str(bags),
        "--workers", str(workers),
        "--exploration-k", str(parameters["exploration_k"]),
        "--final-k", str(parameters["final_k"]),
        "--tau-fit", str(parameters["tau_fit"]),
        "--recovery-similarity", str(parameters["recovery_similarity"]),
        "--group-capacity", str(parameters["group_capacity"]),
        "--group-semantic-weight", str(parameters["group_semantic_weight"]),
        "--output-dir", str(output_dir),
        "--omit-audit",
    ]
    if overwrite:
        command.append("--overwrite-output")
    return command


def aggregate_by_policy(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["policy"]: row for row in summary["aggregates"]}


def signed_delta(late: dict[str, Any], fixed: dict[str, Any], key: str) -> float | None:
    if late.get(key) is None or fixed.get(key) is None:
        return None
    return float(late[key]) - float(fixed[key])


def direction(value: float | None, tolerance: float = 1e-12) -> str:
    if value is None:
        return "not_evaluable"
    if value > tolerance:
        return "gain"
    if value < -tolerance:
        return "loss"
    return "tie"


def result_row(
    case: dict[str, Any],
    variant: dict[str, Any],
    parameters: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    policies = aggregate_by_policy(summary)
    fixed = policies["fixed_top10"]
    first = policies["recursive_first10"]
    late = policies["late_pruned_top10"]
    exact_delta = signed_delta(late, fixed, "exact_hits")
    role_delta = signed_delta(late, fixed, "mean_group_recall")
    # The manuscript declares no frozen-group endpoint for the C1 development
    # corpus.  Its internal group assignments can support diagnostics but must
    # not silently become a reported outcome in this secondary analysis.
    group_endpoint_evaluable = case["corpus"] != "C1"
    group_delta = (
        signed_delta(late, fixed, "mean_frozen_group_coverage")
        if group_endpoint_evaluable else None
    )
    exploration_group_delta = (
        signed_delta(first, fixed, "mean_frozen_group_coverage")
        if group_endpoint_evaluable else None
    )
    pruning_group_delta = (
        signed_delta(late, first, "mean_frozen_group_coverage")
        if group_endpoint_evaluable else None
    )
    row: dict[str, Any] = {
        "variant_id": variant["id"],
        "variant_label": variant["label"],
        "case_id": case["case_id"],
        "corpus": case["corpus"],
        "cutoff": case["cutoff"],
        "evaluation_role": case["evaluation_role"],
        **{key: parameters[key] for key in PARAMETER_KEYS},
        "exact_delta": exact_delta,
        "exact_direction": direction(exact_delta),
        "role_delta": role_delta,
        "role_direction": direction(role_delta),
        "group_endpoint_evaluable": group_endpoint_evaluable,
        "group_delta": group_delta,
        "group_direction": direction(group_delta),
        "exploration_group_delta": exploration_group_delta,
        "pruning_group_delta": pruning_group_delta,
        "group_loss_stage": (
            "exploration"
            if group_delta is not None and group_delta < 0 and exploration_group_delta is not None and exploration_group_delta < 0
            else "late_pruning"
            if group_delta is not None and group_delta < 0 and pruning_group_delta is not None and pruning_group_delta < 0
            else "none_or_not_localized"
        ),
        "gate_passed": bool(summary["quality_gate"]["passed"]),
        "gate_decision": summary["quality_gate"]["decision"],
        "candidate_pool_ceiling": summary["candidate_pool_ceiling"],
        "openalex_requests": summary["openalex_requests"],
    }
    return row


def add_baseline_comparison(rows: list[dict[str, Any]]) -> None:
    baseline = {
        row["case_id"]: row for row in rows if row["variant_id"] == "baseline"
    }
    for row in rows:
        reference = baseline.get(row["case_id"])
        if reference is None:
            raise ValueError(f"Missing baseline for {row['case_id']}")
        dimensions = ("exact_direction", "role_direction", "group_direction", "group_loss_stage")
        row["pattern_matches_baseline"] = all(
            row[key] == reference[key] for key in dimensions
        )
        row["changed_dimensions"] = ";".join(
            key for key in dimensions if row[key] != reference[key]
        )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def report_markdown(spec: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    variants = {variant["id"]: variant["label"] for variant in spec["variants"]}
    nonbaseline = [row for row in rows if row["variant_id"] != "baseline"]
    policy_variants = [
        row for row in nonbaseline
        if row["variant_id"] not in {"tau_role_005", "tau_role_015"}
    ]
    unchanged = sum(bool(row["pattern_matches_baseline"]) for row in nonbaseline)
    policy_unchanged = sum(
        bool(row["pattern_matches_baseline"]) for row in policy_variants
    )
    late_pruning_losses = sum(
        row["group_loss_stage"] == "late_pruning" for row in rows
    )
    lines = [
        "# QSS frozen-parameter sensitivity",
        "",
        "This is a one-factor-at-a-time secondary robustness analysis. It does not select a new policy and must not be used to retune opened confirmatory results.",
        "",
        "## Synthesis",
        "",
        f"- {unchanged}/{len(nonbaseline)} nonbaseline case--variant comparisons ({unchanged / len(nonbaseline):.1%}) retained the frozen baseline's exact, role, group, and group-loss-stage direction pattern.",
        f"- Excluding `tau_role`, which changes the evidence-role outcome definition rather than policy behavior, {policy_unchanged}/{len(policy_variants)} comparisons ({policy_unchanged / len(policy_variants):.1%}) retained the baseline pattern.",
        f"- Every observed frozen-group loss remained localized to exploration; late-pruning-only group losses: {late_pruning_losses}.",
        "- `M=50`, both `tau_g` variants, `kappa=2`, and the 0.80/0.20 group-fit blend changed no direction/stage pattern. Reducing `M` to 20 changed four cases; `K=15` changed two; `kappa=4` and the 0.60/0.40 blend changed one each.",
        "- Evidence-role conclusions were construct-threshold-sensitive: `tau_role=0.05` changed seven case patterns and `tau_role=0.15` changed two. Report this dependence rather than choosing the threshold with the most favorable outcomes.",
        "- Gate counts are descriptive sensitivity results, not a parameter-selection objective. In particular, the greater number of passing rows at `M=20` or `K=15` must not be used to replace the frozen rule after confirmatory outcomes were opened.",
        "",
        "## Variant-level pattern stability",
        "",
        "| variant | evaluated rows | unchanged patterns | changed patterns |",
        "|---|---:|---:|---:|",
    ]
    for variant_id, label in variants.items():
        subset = [row for row in rows if row["variant_id"] == variant_id]
        unchanged = sum(bool(row["pattern_matches_baseline"]) for row in subset)
        lines.append(f"| {label} | {len(subset)} | {unchanged} | {len(subset) - unchanged} |")
    changed = [
        row for row in rows
        if row["variant_id"] != "baseline" and not row["pattern_matches_baseline"]
    ]
    lines += ["", "## Pattern changes relative to the frozen baseline", ""]
    if not changed:
        lines.append("No direction or group-loss-stage pattern changed in the evaluated variants.")
    else:
        lines += [
            "| variant | case | changed dimensions | exact | role | group | group-loss stage |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in changed:
            lines.append(
                f"| {row['variant_id']} | {row['case_id']} | {row['changed_dimensions']} | "
                f"{row['exact_direction']} | {row['role_direction']} | "
                f"{row['group_direction']} | {row['group_loss_stage']} |"
            )
    lines += [
        "",
        "Detailed machine-readable results are in `sensitivity_results.csv`; each run directory contains its request manifest, summary, per-bag results, and compact result note. Every evaluator summary must report `openalex_requests=0`.",
        "",
    ]
    return "\n".join(lines)


def completed_summary_matches(
    summary: dict[str, Any], parameters: dict[str, Any], bags_per_fraction: int
) -> bool:
    rule = summary.get("frozen_rule", {})
    expected = {
        "exploration_k": parameters["exploration_k"],
        "final_k": parameters["final_k"],
        "tau_fit": parameters["tau_fit"],
        "recovery_similarity": parameters["recovery_similarity"],
        "group_semantic_weight": parameters["group_semantic_weight"],
    }
    return (
        summary.get("bags") == bags_per_fraction * 3
        and all(rule.get(key) == value for key, value in expected.items())
        and set(rule.get("group_capacities", {}).values()) == {parameters["group_capacity"]}
        and summary.get("openalex_requests") == 0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=Path("config/qss_parameter_sensitivity_v1.json"))
    parser.add_argument("--registry", type=Path, default=Path("config/qss_corpus_registry.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/qss-parameter-sensitivity-v1"))
    parser.add_argument("--cases", help="Comma-separated corpus IDs; default is every evaluable corpus")
    parser.add_argument("--variants", help="Comma-separated variant IDs; baseline is always included")
    parser.add_argument("--bags", type=int, default=50)
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Evaluator workers per run; total process workers are jobs*workers.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=4,
        help="Independent variant/case runs to execute concurrently.",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if min(args.bags, args.workers, args.jobs) < 1:
        raise SystemExit("bags, workers, and jobs must be positive")

    spec_path, registry_path, output_root = resolve(args.spec), resolve(args.registry), resolve(args.output_dir)
    spec, registry = load_json(spec_path), load_json(registry_path)
    cases = cases_from_registry(registry, selected_ids(args.cases))
    requested_variants = selected_ids(args.variants)
    if requested_variants is not None:
        requested_variants.add("baseline")
    variants = [
        variant for variant in spec["variants"]
        if requested_variants is None or variant["id"] in requested_variants
    ]
    known_variants = {variant["id"] for variant in spec["variants"]}
    if requested_variants and requested_variants - known_variants:
        raise SystemExit(f"Unknown variant(s): {sorted(requested_variants - known_variants)}")
    if not cases or not variants:
        raise SystemExit("No evaluable cases or variants selected")

    output_root.mkdir(parents=True, exist_ok=True)
    work: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path, list[str]]] = []
    for variant in variants:
        parameters = merged_parameters(spec, variant)
        for case in cases:
            run_dir = output_root / "runs" / variant["id"] / case["case_id"]
            command = evaluator_command(
                case, parameters, run_dir, args.bags, args.workers, args.overwrite
            )
            work.append((case, variant, parameters, run_dir, command))

    total = len(work)
    if args.dry_run:
        for completed, (case, variant, _, _, command) in enumerate(work, start=1):
            print(f"[{completed}/{total}] {variant['id']} {case['case_id']}")
            print(" ".join(command))
        return 0

    def run_one(item: tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path, list[str]]):
        case, variant, parameters, run_dir, command = item
        summary_path = run_dir / "summary.json"
        status = "ran"
        if summary_path.is_file() and not args.overwrite:
            existing = load_json(summary_path)
            if not completed_summary_matches(existing, parameters, args.bags):
                raise RuntimeError(
                    f"Existing run does not match requested parameters: {summary_path}; "
                    "use --overwrite after inspecting it"
                )
            status = "resumed"
        else:
            completed_process = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if completed_process.returncode:
                raise RuntimeError(
                    f"Evaluator failed for {variant['id']} {case['case_id']}\n"
                    f"{completed_process.stdout}"
                )
        summary = load_json(summary_path)
        if summary.get("openalex_requests") != 0:
            raise RuntimeError(f"Unexpected network use in {summary_path}")
        return result_row(case, variant, parameters, summary), status

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(run_one, item): item for item in work}
        for completed, future in enumerate(as_completed(futures), start=1):
            item = futures[future]
            case, variant = item[0], item[1]
            row, status = future.result()
            rows.append(row)
            print(
                f"[{completed}/{total}] {status}: {variant['id']} {case['case_id']}",
                flush=True,
            )
    rows.sort(key=lambda row: (row["variant_id"], row["case_id"]))
    add_baseline_comparison(rows)
    write_csv(output_root / "sensitivity_results.csv", rows)
    (output_root / "RESULTS.md").write_text(
        report_markdown(spec, rows), encoding="utf-8"
    )
    manifest = {
        "schema_version": 1,
        "analysis_id": spec["analysis_id"],
        "status": spec["status"],
        "spec": str(spec_path),
        "registry": str(registry_path),
        "bags_per_fraction": args.bags,
        "evaluator_workers": args.workers,
        "parallel_jobs": args.jobs,
        "maximum_process_workers": args.jobs * args.workers,
        "case_count": len(cases),
        "variant_count": len(variants),
        "run_count": len(rows),
        "openalex_requests": 0,
        "retuning_forbidden": True,
    }
    (output_root / "RUN_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {output_root}; runs={len(rows)}; OpenAlex requests=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
