#!/usr/bin/env python3
"""Restore withheld abstracts in released QSS candidate pools from OpenAlex.

The public deposit replaces each candidate abstract with its SHA-256 digest.
This script re-fetches the OpenAlex abstract inverted index, rebuilds the text
with the same function the pipeline used, and writes it back only when the
digest matches. Mismatches are reported, not silently accepted, because a
changed abstract would change the TF-IDF representation and the replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scholarly_radar.openalex import reconstruct_abstract  # noqa: E402

API = "https://api.openalex.org/works"
BATCH = 50


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_id(openalex_id: str) -> str:
    return openalex_id.rstrip("/").rsplit("/", 1)[-1]


def fetch_inverted_indexes(ids: list[str], mailto: str | None) -> dict[str, dict | None]:
    params = {
        "filter": "openalex_id:" + "|".join(ids),
        "select": "id,abstract_inverted_index",
        "per-page": str(len(ids)),
    }
    if mailto:
        params["mailto"] = mailto
    with urllib.request.urlopen(f"{API}?{urllib.parse.urlencode(params)}", timeout=60) as response:
        payload = json.load(response)
    return {short_id(work["id"]): work.get("abstract_inverted_index") for work in payload["results"]}


def withheld_records(payload: Any) -> list[dict[str, Any]]:
    """Return every record dict that carries a withheld abstract digest."""
    found: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if payload.get("abstract_sha256") and payload.get("openalex_id"):
            found.append(payload)
        for value in payload.values():
            found.extend(withheld_records(value))
    elif isinstance(payload, list):
        for value in payload:
            found.extend(withheld_records(value))
    return found


def rehydrate(payload: Any, fetch: Callable[[list[str]], dict[str, dict | None]]) -> dict[str, int]:
    records = withheld_records(payload)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_id.setdefault(short_id(record["openalex_id"]), []).append(record)
    counts = {"records": len(records), "restored": 0, "mismatch": 0, "missing": 0}
    ids = sorted(by_id)
    for start in range(0, len(ids), BATCH):
        fetched = fetch(ids[start:start + BATCH])
        for work_id in ids[start:start + BATCH]:
            text = reconstruct_abstract(fetched.get(work_id)) or ""
            for record in by_id[work_id]:
                if not text:
                    counts["missing"] += 1
                elif digest(text) == record["abstract_sha256"]:
                    record["abstract"] = text
                    counts["restored"] += 1
                else:
                    counts["mismatch"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pools", nargs="+", type=Path, help="Released qss_candidates_*.json files")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mailto", help="Contact address for the OpenAlex polite pool")
    parser.add_argument("--pause", type=float, default=0.2, help="Seconds between requests")
    args = parser.parse_args()

    def fetch(ids: list[str]) -> dict[str, dict | None]:
        time.sleep(args.pause)
        return fetch_inverted_indexes(ids, args.mailto)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {}
    for pool in args.pools:
        payload = json.loads(pool.read_text(encoding="utf-8"))
        report[pool.as_posix()] = rehydrate(payload, fetch)
        (args.output_dir / pool.name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(pool.name, report[pool.as_posix()])
    (args.output_dir / "REHYDRATION_REPORT.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
