#!/usr/bin/env python3
"""Confirm claim-relevant 75% pool sensitivity rows with 50 bags."""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import run_qss_parameter_sensitivity as parameter_sensitivity
import run_qss_pool_size_sensitivity as pool_sensitivity


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def normalized_reference(row: dict[str, str]) -> dict[str, str]:
    result = dict(row)
    if float(result["candidate_pool_ceiling"]) == 0:
        result["exact_direction"] = "not_evaluable"
    return result


def report(rows: list[dict[str, Any]], fraction: float, selected_after_screening: bool) -> str:
    pattern_keys = pool_sensitivity.PATTERN_KEYS
    match_10 = sum(
        all(row[key] == row[f"screening_{key}"] for key in pattern_keys)
        for row in rows
    )
    match_full = sum(bool(row["confirmation_pattern_matches_full50"]) for row in rows)
    reversals = Counter()
    for row in rows:
        for key in ("exact_direction", "role_direction", "group_direction"):
            if pool_sensitivity.sign_reversal(row[f"full50_{key}"], row[key]):
                reversals[key] += 1
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_case[row["case_id"]].append(row)
    title = (
        "QSS 50-bag confirmation of screened pool-size changes"
        if selected_after_screening
        else f"QSS 50-bag complete {fraction:.0%}-pool sensitivity"
    )
    scope = (
        f"This targeted confirmation reruns the {len(rows)} deterministic {fraction:.0%}-pool rows that changed at least one direction, endpoint-evaluability state, or group-loss-stage label in the 10-bag screening analysis. It is not an unbiased stability-frequency estimate."
        if selected_after_screening
        else f"This complete confirmation reruns all {len(rows)} deterministic {fraction:.0%}-pool case-replicates with 50 bags, matching the frozen full-pool analysis."
    )
    comparison_line = (
        f"- {len(rows) - match_full}/{len(rows)} remained different from the frozen full-pool 50-bag pattern; {match_full} returned to the full-pool pattern."
        if selected_after_screening
        else f"- {match_full}/{len(rows)} matched the frozen full-pool 50-bag pattern; {len(rows) - match_full} differed."
    )
    lines = [
        f"# {title}",
        "",
        scope,
        "",
        "## Confirmation summary",
        "",
        f"- {match_10}/{len(rows)} case-replicates retained the complete 10-bag screening pattern at 50 bags.",
        comparison_line,
        f"- Sign reversals versus the full-pool 50-bag result: exact {reversals['exact_direction']}, evidence-role {reversals['role_direction']}, frozen-group {reversals['group_direction']}.",
        "",
        "| case | case-replicates | retained screening pattern | remained different from full pool |",
        "|---|---:|---:|---:|",
    ]
    for case_id in sorted(by_case):
        subset = by_case[case_id]
        retained = sum(
            all(row[key] == row[f"screening_{key}"] for key in pattern_keys)
            for row in subset
        )
        different = sum(not row["confirmation_pattern_matches_full50"] for row in subset)
        lines.append(f"| {case_id} | {len(subset)} | {retained} | {different} |")
    lines += [
        "",
        "## Interpretation boundary",
        "",
        (
            "The targeted rows were selected after the 10-bag screening pass, so these counts describe persistence among flagged rows, not population probabilities. Group definitions and rules are frozen, but text representations, calibration, and record-level assignments are recomputed per pool. Pool size, membership, and representation vary together; retrieval rank is not simulated."
            if selected_after_screening
            else "These are deterministic design-point frequencies, not sampling probabilities from a known retrieval population. Group definitions and rules are frozen, but text representations, calibration, and record-level assignments are recomputed per pool. Pool size, membership, and representation vary together; retrieval rank is not simulated. Pattern matches include jointly undefined endpoints. The same-data 50-bag rerun is not independent validation."
        ),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--screening-results", type=Path,
        default=Path("outputs/qss-pool-size-sensitivity-v1/pool_size_sensitivity_results.csv"),
    )
    parser.add_argument(
        "--full50-results", type=Path,
        default=Path("outputs/qss-parameter-sensitivity-v1/sensitivity_results.csv"),
    )
    parser.add_argument("--spec", type=Path, default=Path("config/qss_pool_size_sensitivity_v1.json"))
    parser.add_argument("--registry", type=Path, default=Path("config/qss_corpus_registry.json"))
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("outputs/qss-pool-size-sensitivity-confirmation-50bags-v1"),
    )
    parser.add_argument("--fraction", type=float, default=0.75)
    parser.add_argument("--bags", type=int, default=50)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--repair-incompatible", action="store_true")
    parser.add_argument(
        "--all-rows", action="store_true",
        help="Confirm every row at the requested fraction instead of only screening changes.",
    )
    parser.add_argument(
        "--pool-work-dir", type=Path,
        default=Path("/tmp/qss-pool-size-confirmation-50bags-v1"),
    )
    args = parser.parse_args()
    if min(args.bags, args.workers, args.jobs) < 1:
        raise SystemExit("bags, workers, and jobs must be positive")

    screening_path = pool_sensitivity.resolve(args.screening_results)
    full50_path = pool_sensitivity.resolve(args.full50_results)
    spec_path = pool_sensitivity.resolve(args.spec)
    registry_path = pool_sensitivity.resolve(args.registry)
    output_root = pool_sensitivity.resolve(args.output_dir)
    spec = pool_sensitivity.load_json(spec_path)
    registry = pool_sensitivity.load_json(registry_path)
    cases = {
        case["case_id"]: case
        for case in parameter_sensitivity.cases_from_registry(registry)
    }
    fraction_rows = [
        row for row in read_csv(screening_path)
        if math.isclose(float(row["pool_fraction"]), args.fraction)
    ]
    selected = (
        fraction_rows if args.all_rows
        else [row for row in fraction_rows if row["pattern_matches_full_pool"] == "False"]
    )
    if not selected:
        raise SystemExit("No changed screening rows selected")
    full50 = {
        row["case_id"]: normalized_reference(row)
        for row in read_csv(full50_path)
        if row["variant_id"] == "baseline"
    }
    parameters = dict(spec["baseline"])
    namespace = str(spec["sampling"]["namespace"])
    output_root.mkdir(parents=True, exist_ok=True)
    pool_work_root = args.pool_work_dir.expanduser().resolve()

    def run_one(screening: dict[str, str]):
        case = cases[screening["case_id"]]
        replicate = int(screening["pool_replicate"])
        pool_path = (
            pool_work_root / case["case_id"] / pool_sensitivity.fraction_id(args.fraction)
            / f"r{replicate:02d}.json"
        )
        sampling = pool_sensitivity.build_subset_pool(
            case["candidate_pool"], pool_path, case["case_id"], args.fraction,
            replicate, namespace,
        )
        run_dir = (
            output_root / "runs" / case["case_id"]
            / pool_sensitivity.fraction_id(args.fraction) / f"r{replicate:02d}"
        )
        summary_path = run_dir / "summary.json"
        status = "ran"
        try:
            force = False
            if summary_path.exists():
                summary = pool_sensitivity.load_json(summary_path)
                compatible = (
                    summary.get("bags") == args.bags * 3
                    and summary.get("candidate_pool") == sampling["selected_count"]
                    and summary.get("openalex_requests") == 0
                    and "candidate_scope_eligible_count" in summary
                )
                if compatible:
                    status = "resumed"
                elif args.repair_incompatible:
                    status, force = "repaired", True
                else:
                    raise RuntimeError(f"Existing run is incompatible: {summary_path}")
            if status != "resumed":
                command = pool_sensitivity.evaluator_command(
                    case, pool_path, parameters, run_dir, args.bags, args.workers, force
                )
                completed = subprocess.run(
                    command, cwd=PROJECT_ROOT, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
                )
                if completed.returncode:
                    raise RuntimeError(
                        f"Evaluator failed for {case['case_id']} r{replicate}\n{completed.stdout}"
                    )
                summary = pool_sensitivity.load_json(summary_path)
            row = pool_sensitivity.result_row(
                case, args.fraction, replicate, sampling, parameters, summary
            )
            reference = full50[case["case_id"]]
            for key in pool_sensitivity.PATTERN_KEYS:
                row[f"screening_{key}"] = screening[key]
                row[f"full50_{key}"] = reference[key]
            row["confirmation_pattern_matches_screening"] = all(
                row[key] == screening[key] for key in pool_sensitivity.PATTERN_KEYS
            )
            row["confirmation_pattern_matches_full50"] = all(
                row[key] == reference[key] for key in pool_sensitivity.PATTERN_KEYS
            )
            return row, status
        finally:
            pool_path.unlink(missing_ok=True)

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {executor.submit(run_one, row): row for row in selected}
        for completed_count, future in enumerate(as_completed(futures), start=1):
            screening = futures[future]
            row, status = future.result()
            rows.append(row)
            print(
                f"[{completed_count}/{len(selected)}] {status}: {screening['case_id']} "
                f"r{int(screening['pool_replicate']):02d}", flush=True,
            )
    rows.sort(key=lambda row: (row["case_id"], row["pool_replicate"]))
    write_csv(output_root / "confirmation_results.csv", rows)
    (output_root / "RESULTS.md").write_text(
        report(rows, args.fraction, selected_after_screening=not args.all_rows),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "analysis_id": "qss-pool-size-screening-confirmation-50bags-v1",
        "selection_rule": (
            f"all {args.fraction:.0%}-pool rows"
            if args.all_rows
            else f"{args.fraction:.0%}-pool rows with pattern_matches_full_pool=False in 10-bag screening"
        ),
        "screening_results": str(screening_path.resolve()),
        "screening_sha256": pool_sensitivity.sha256_file(screening_path),
        "full50_results": str(full50_path.resolve()),
        "full50_sha256": pool_sensitivity.sha256_file(full50_path),
        "fraction": args.fraction,
        "selected_rows": len(rows),
        "bags_per_seed_fraction": args.bags,
        "openalex_requests": 0,
        "cache_only": True,
    }
    (output_root / "RUN_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {output_root}; confirmed={len(rows)}; OpenAlex requests=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
