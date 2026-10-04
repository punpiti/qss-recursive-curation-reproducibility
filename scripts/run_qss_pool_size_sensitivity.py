#!/usr/bin/env python3
"""Run deterministic, cache-only candidate-pool size sensitivity for QSS.

The frozen candidate files do not contain retrieval rank. This analysis uses
SHA-256-ordered, discovery-channel-stratified nested subsets and therefore
tests sensitivity to pool quantity/composition, not a real retrieval top-N.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import run_qss_parameter_sensitivity as parameter_sensitivity


EVALUATOR = PROJECT_ROOT / "scripts/evaluate_qss_late_pruner.py"
PATTERN_KEYS = (
    "exact_direction",
    "role_direction",
    "group_direction",
    "group_loss_stage",
)


def resolve(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_identifier(record: dict[str, Any]) -> str:
    for key in ("openalex_id", "doi", "title"):
        value = str(record.get(key) or "").strip().lower()
        if value:
            return f"{key}:{value}"
    raise ValueError("Candidate has no openalex_id, DOI, or title")


def channel_signature(record: dict[str, Any]) -> str:
    value = record.get("discovery_channels")
    if isinstance(value, list):
        parts = sorted({str(item).strip() for item in value if str(item).strip()})
        return ";".join(parts) or "missing"
    return str(value or "missing").strip() or "missing"


def stable_digest(*parts: object) -> str:
    return sha256_bytes("|".join(map(str, parts)).encode("utf-8"))


def stratified_nested_order(
    records: list[dict[str, Any]], case_id: str, replicate: int, namespace: str
) -> list[int]:
    """Return one deterministic proportionally interleaved record order."""
    identities = [canonical_identifier(record) for record in records]
    if len(set(identities)) != len(identities):
        duplicates = len(identities) - len(set(identities))
        raise ValueError(f"{case_id}: {duplicates} duplicate canonical candidate identifiers")
    strata: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        strata[channel_signature(record)].append(index)
    for signature, indices in strata.items():
        indices.sort(
            key=lambda index: stable_digest(
                namespace, case_id, replicate, signature, identities[index]
            )
        )
    used = {signature: 0 for signature in strata}
    ordered: list[int] = []
    while len(ordered) < len(records):
        available = [signature for signature in strata if used[signature] < len(strata[signature])]
        signature = min(
            available,
            key=lambda item: (
                (used[item] + 1) / len(strata[item]),
                stable_digest(namespace, case_id, replicate, "stratum", item),
            ),
        )
        ordered.append(strata[signature][used[signature]])
        used[signature] += 1
    return ordered


def subset_indices(order: list[int], fraction: float) -> list[int]:
    if not 0 < fraction <= 1:
        raise ValueError("Pool fractions must be in (0, 1]")
    target = min(len(order), max(1, int(math.floor(len(order) * fraction + 0.5))))
    return sorted(order[:target])


def fraction_id(fraction: float) -> str:
    return f"f{int(round(fraction * 100)):03d}"


def build_subset_pool(
    source: Path,
    destination: Path,
    case_id: str,
    fraction: float,
    replicate: int,
    namespace: str,
) -> dict[str, Any]:
    payload = load_json(source)
    records = payload.get("candidate_records", payload.get("recommendations", []))
    if not records:
        raise ValueError(f"No candidate records in {source}")
    order = stratified_nested_order(records, case_id, replicate, namespace)
    indices = subset_indices(order, fraction)
    selected = [records[index] for index in indices]
    result = dict(payload)
    key = "candidate_records" if "candidate_records" in payload else "recommendations"
    result[key] = selected
    result["pool_size_sensitivity_sampling"] = {
        "schema_version": 1,
        "source": str(source.resolve()),
        "source_sha256": sha256_file(source),
        "case_id": case_id,
        "requested_fraction": fraction,
        "source_count": len(records),
        "selected_count": len(selected),
        "actual_fraction": len(selected) / len(records),
        "replicate": replicate,
        "namespace": namespace,
        "stratum": "exact stored discovery_channels signature",
        "nested_order_sha256": sha256_bytes(
            "\n".join(canonical_identifier(records[index]) for index in order).encode("utf-8")
        ),
        "selected_identifiers_sha256": sha256_bytes(
            "\n".join(canonical_identifier(record) for record in selected).encode("utf-8")
        ),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if destination.exists() and destination.read_text(encoding="utf-8") != content:
        raise RuntimeError(f"Refusing to replace nonmatching deterministic pool: {destination}")
    destination.write_text(content, encoding="utf-8")
    return result["pool_size_sensitivity_sampling"]


def evaluator_command(
    case: dict[str, Any], pool: Path, parameters: dict[str, Any], output: Path,
    bags: int, workers: int, overwrite: bool,
) -> list[str]:
    command = [
        sys.executable, str(EVALUATOR),
        "--bib", str(case["bib"]),
        "--candidate-pool", str(pool),
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
        "--output-dir", str(output),
        "--omit-audit",
    ]
    if overwrite:
        command.append("--overwrite-output")
    return command


def normalized_delta(late: dict[str, Any], fixed: dict[str, Any], key: str) -> float | None:
    if late.get(key) is None or fixed.get(key) is None:
        return None
    return float(late[key]) - float(fixed[key])


def result_row(
    case: dict[str, Any], fraction: float, replicate: int,
    sampling: dict[str, Any], parameters: dict[str, Any], summary: dict[str, Any],
) -> dict[str, Any]:
    variant = {"id": fraction_id(fraction), "label": f"Pool fraction {fraction:.2f}"}
    row = parameter_sensitivity.result_row(case, variant, parameters, summary)
    policies = parameter_sensitivity.aggregate_by_policy(summary)
    fixed, late = policies["fixed_top10"], policies["late_pruned_top10"]
    exact_endpoint_evaluable = int(summary["reachable_holdout_count"]) > 0
    if not exact_endpoint_evaluable:
        row["exact_delta"] = None
        row["exact_direction"] = "not_evaluable"
    row.update({
        "pool_fraction": fraction,
        "pool_replicate": replicate,
        "source_pool_size": sampling["source_count"],
        "subset_pool_size": sampling["selected_count"],
        "actual_pool_fraction": sampling["actual_fraction"],
        "subset_pool_sha256": sampling["selected_identifiers_sha256"],
        "candidate_scope_eligible_count": summary["candidate_scope_eligible_count"],
        "candidate_scope_ineligible_count": summary["candidate_scope_ineligible_count"],
        "candidate_scope_eligible_rate": summary["candidate_scope_eligible_rate"],
        "candidate_scope_reachable_group_count": summary["candidate_scope_reachable_group_count"],
        "eligible_group_counts": json.dumps(summary["candidate_scope_eligible_group_counts"], sort_keys=True),
        "exact_endpoint_evaluable": exact_endpoint_evaluable,
        "fixed_exact_recall": fixed["bag_positive_recall"] if exact_endpoint_evaluable else None,
        "recursive_exact_recall": late["bag_positive_recall"] if exact_endpoint_evaluable else None,
        "exact_recall_delta": (
            normalized_delta(late, fixed, "bag_positive_recall")
            if exact_endpoint_evaluable else None
        ),
        "fixed_role_recall": fixed["mean_group_recall"],
        "recursive_role_recall": late["mean_group_recall"],
        "fixed_group_coverage": fixed["mean_frozen_group_coverage"],
        "recursive_group_coverage": late["mean_frozen_group_coverage"],
    })
    return row


def add_full_pool_comparison(rows: list[dict[str, Any]]) -> None:
    full = {
        row["case_id"]: row for row in rows
        if math.isclose(float(row["pool_fraction"]), 1.0)
    }
    for row in rows:
        reference = full[row["case_id"]]
        row["pattern_matches_full_pool"] = all(row[key] == reference[key] for key in PATTERN_KEYS)
        row["changed_dimensions_vs_full_pool"] = ";".join(
            key for key in PATTERN_KEYS if row[key] != reference[key]
        )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def sign_reversal(reference: str, observed: str) -> bool:
    return (reference, observed) in {("gain", "loss"), ("loss", "gain")}


def report_markdown(spec: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    full = {
        row["case_id"]: row for row in rows
        if math.isclose(float(row["pool_fraction"]), 1.0)
    }
    lines = [
        "# QSS deterministic candidate-pool size sensitivity",
        "",
        "This is a cache-only secondary robustness analysis under the frozen machine scope/group definitions. The source files do not preserve retrieval rank, so these results concern pool quantity and composition rather than a real retrieval top-N cutoff.",
        "",
        "## Fraction-level stability",
        "",
        "| pool fraction | case-replicates | complete pattern match vs full pool | exact endpoint lost | exact / role / group sign reversals | mean machine-eligible count | mean eligible rate |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    fractions = sorted({float(row["pool_fraction"]) for row in rows})
    for fraction in fractions:
        subset = [row for row in rows if math.isclose(float(row["pool_fraction"]), fraction)]
        matches = sum(bool(row["pattern_matches_full_pool"]) for row in subset)
        eligible_counts = [float(row["candidate_scope_eligible_count"]) for row in subset]
        eligible_rates = [float(row["candidate_scope_eligible_rate"]) for row in subset]
        exact_lost = sum(
            bool(full[row["case_id"]]["exact_endpoint_evaluable"])
            and not bool(row["exact_endpoint_evaluable"])
            for row in subset
        )
        reversals = [
            sum(sign_reversal(full[row["case_id"]][key], row[key]) for row in subset)
            for key in ("exact_direction", "role_direction", "group_direction")
        ]
        lines.append(
            f"| {fraction:.0%} | {len(subset)} | {matches}/{len(subset)} ({matches / len(subset):.1%}) | "
            f"{exact_lost} | {' / '.join(map(str, reversals))} | "
            f"{mean(eligible_counts):.1f} | {mean(eligible_rates):.1%} |"
        )
    changed = [
        row for row in rows
        if float(row["pool_fraction"]) < 1 and not row["pattern_matches_full_pool"]
    ]
    lines += [
        "",
        "## Observed pattern changes",
        "",
        f"Across reduced pools, {len(changed)}/{sum(float(row['pool_fraction']) < 1 for row in rows)} deterministic case-replicates changed at least one direction, endpoint-evaluability state, or group-loss-stage label relative to the full pool.",
        "",
    ]
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in changed:
        by_case[row["case_id"]].append(row)
    if by_case:
        lines += [
            "| case | changed / reduced replicates | affected fractions | changed dimensions |",
            "|---|---:|---|---|",
        ]
        for case_id in sorted(by_case):
            case_rows = by_case[case_id]
            denominator = sum(
                row["case_id"] == case_id and float(row["pool_fraction"]) < 1
                for row in rows
            )
            fractions_text = ", ".join(
                f"{value:.0%}" for value in sorted({float(row["pool_fraction"]) for row in case_rows})
            )
            dimensions = sorted({
                item
                for row in case_rows
                for item in str(row["changed_dimensions_vs_full_pool"]).split(";")
                if item
            })
            lines.append(
                f"| {case_id} | {len(case_rows)}/{denominator} | {fractions_text} | {', '.join(dimensions)} |"
            )
    else:
        lines.append("No reduced-pool replicate changed the full-pool direction/stage pattern.")
    lines += [
        "",
        "## Interpretation boundaries",
        "",
        "- Replicates are deterministic design points, not random draws from a known retrieval population; their proportions are stability frequencies, not sampling probabilities.",
        "- `machine-eligible` means the frozen automated scope/group gate passed. It is not a human or LLM judgment of substantive fit.",
        "- A zero retrieval ceiling is reported but cannot diagnose ranking ability.",
        "- Detailed results are in `pool_size_sensitivity_results.csv`; every evaluator run reports `openalex_requests=0`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=Path("config/qss_pool_size_sensitivity_v1.json"))
    parser.add_argument("--registry", type=Path, default=Path("config/qss_corpus_registry.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/qss-pool-size-sensitivity-v1"))
    parser.add_argument("--cases", help="Comma-separated corpus IDs")
    parser.add_argument("--fractions", help="Comma-separated override")
    parser.add_argument("--replicates", type=int)
    parser.add_argument("--bags", type=int, default=50)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument(
        "--pool-work-dir", type=Path,
        default=Path("/tmp/qss-pool-size-sensitivity-v1-pools"),
        help="Temporary derived pools; each is deleted after its evaluator run.",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--repair-incompatible", action="store_true",
        help="Overwrite only an existing run whose bag count or schema is incompatible.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if min(args.bags, args.workers, args.jobs) < 1:
        raise SystemExit("bags, workers, and jobs must be positive")

    spec_path, registry_path, output_root = resolve(args.spec), resolve(args.registry), resolve(args.output_dir)
    pool_work_root = args.pool_work_dir.expanduser().resolve()
    spec, registry = load_json(spec_path), load_json(registry_path)
    cases = parameter_sensitivity.cases_from_registry(
        registry, parameter_sensitivity.selected_ids(args.cases)
    )
    parameters = dict(spec["baseline"])
    fractions = (
        [float(item) for item in args.fractions.split(",") if item.strip()]
        if args.fractions else [float(item) for item in spec["pool_fractions"]]
    )
    fractions = sorted(set(fractions))
    if 1.0 not in fractions:
        fractions.append(1.0)
    replicates = args.replicates or int(spec["deterministic_replicates"])
    if replicates < 1:
        raise SystemExit("replicates must be positive")
    namespace = str(spec["sampling"]["namespace"])
    output_root.mkdir(parents=True, exist_ok=True)

    work: list[tuple[dict[str, Any], float, int, Path, Path]] = []
    for case in cases:
        for replicate in range(replicates):
            for fraction in fractions:
                if math.isclose(fraction, 1.0) and replicate > 0:
                    continue
                pool_path = pool_work_root / case["case_id"] / fraction_id(fraction) / f"r{replicate:02d}.json"
                run_dir = output_root / "runs" / case["case_id"] / fraction_id(fraction) / f"r{replicate:02d}"
                work.append((case, fraction, replicate, pool_path, run_dir))

    if args.dry_run:
        print(f"Prepared {len(work)} deterministic pool evaluations")
        return 0

    def run_one(item: tuple[dict[str, Any], float, int, Path, Path]):
        case, fraction, replicate, pool_path, run_dir = item
        sampling = build_subset_pool(
            case["candidate_pool"], pool_path, case["case_id"], fraction,
            replicate, namespace,
        )
        summary_path = run_dir / "summary.json"
        status = "ran"
        try:
            force_overwrite = args.overwrite
            if summary_path.exists() and not args.overwrite:
                summary = load_json(summary_path)
                expected_bags = args.bags * 3
                compatible = (
                    summary.get("bags") != expected_bags
                    or summary.get("candidate_pool") != sampling["selected_count"]
                    or summary.get("openalex_requests") != 0
                    or "candidate_scope_eligible_count" not in summary
                ) is False
                if compatible:
                    status = "resumed"
                elif args.repair_incompatible:
                    force_overwrite = True
                    status = "repaired"
                else:
                    raise RuntimeError(f"Existing run is incompatible: {summary_path}")
            if status != "resumed":
                command = evaluator_command(
                    case, pool_path, parameters, run_dir, args.bags, args.workers, force_overwrite
                )
                completed = subprocess.run(
                    command, cwd=PROJECT_ROOT, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
                )
                if completed.returncode:
                    raise RuntimeError(
                        f"Evaluator failed for {case['case_id']} {fraction} r{replicate}\n{completed.stdout}"
                    )
                summary = load_json(summary_path)
            return result_row(case, fraction, replicate, sampling, parameters, summary), status
        finally:
            pool_path.unlink(missing_ok=True)

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(run_one, item): item for item in work}
        for completed_count, future in enumerate(as_completed(futures), start=1):
            item = futures[future]
            row, status = future.result()
            rows.append(row)
            print(
                f"[{completed_count}/{len(work)}] {status}: {item[0]['case_id']} "
                f"{fraction_id(item[1])} r{item[2]:02d}", flush=True,
            )

    rows.sort(key=lambda row: (row["case_id"], row["pool_fraction"], row["pool_replicate"]))
    add_full_pool_comparison(rows)
    write_csv(output_root / "pool_size_sensitivity_results.csv", rows)
    (output_root / "RESULTS.md").write_text(report_markdown(spec, rows), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "analysis_id": spec["analysis_id"],
        "spec": str(spec_path.resolve()),
        "spec_sha256": sha256_file(spec_path),
        "registry": str(registry_path.resolve()),
        "fractions": fractions,
        "deterministic_replicates": replicates,
        "bags_per_seed_fraction": args.bags,
        "case_count": len(cases),
        "evaluation_count": len(work),
        "temporary_pool_work_dir": str(pool_work_root),
        "derived_pools_retained": False,
        "cache_only": True,
        "openalex_requests": 0,
        "python": sys.executable,
    }
    (output_root / "RUN_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {output_root}; evaluations={len(work)}; OpenAlex requests=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
