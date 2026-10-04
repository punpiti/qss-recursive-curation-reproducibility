#!/usr/bin/env python3
"""Synthesize QSS proxy reviews from Claude, Codex, and Gemini as equal AI experts."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "outputs/qss-ai-proxy-review/MANIFEST.json"
SCHEMA_PATH = ROOT / "config/qss_review_response.schema.json"
RESULT_ROOT = ROOT / "outputs/qss-review-results"
OUTPUT_DIR = ROOT / "outputs/qss-ai-expert-synthesis-2026-10-04"
EXPERT_PATTERNS = {
    "Claude": "{batch_id}__ai-proxy__claude-opus-5-5__*.json",
    "Codex": "{batch_id}__ai-proxy__codex-cli-0-159-3__*.json",
    "Gemini": "{batch_id}__ai-proxy__gemini-*.json*",
}
CASE_CODES = {
    "C1": "C1",
    "C2": "C2",
    "C3": "C3",
    "C4": "C4",
    "C5": "C5",
    "C6": "C6",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def majority(values: list[str]) -> tuple[str, int]:
    value, count = Counter(values).most_common(1)[0]
    return (value, count) if count >= 2 else ("NO_CONSENSUS", count)


def cohen_kappa(left: list[str], right: list[str]) -> tuple[float, float]:
    categories = sorted(set(left) | set(right))
    n = len(left)
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / n
    lc, rc = Counter(left), Counter(right)
    expected = sum((lc[c] / n) * (rc[c] / n) for c in categories)
    kappa = (observed - expected) / (1 - expected) if expected < 1 else 1.0
    return observed, kappa


def load_payload(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"ข้อมูลสำหรับตรวจ:\s*```json\s*(\{.*?\})\s*```", text, re.S
    )
    if not match:
        raise ValueError(f"Prompt payload not found: {path}")
    return json.loads(match.group(1))


def pct(numerator: int, denominator: int) -> str:
    return f"{100 * numerator / denominator:.1f}%"


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    reviews: dict[str, dict[str, dict]] = {name: {} for name in EXPERT_PATTERNS}
    row_meta: dict[str, dict] = {}
    source_files: list[dict] = []
    data_quality_issues: list[dict] = []
    expected_order: list[str] = []

    for batch in manifest["batches"]:
        prompt_path = ROOT / batch["path"]
        if sha256(prompt_path) != batch["sha256"]:
            raise AssertionError(f"Prompt hash mismatch: {batch['batch_id']}")
        payload = load_payload(prompt_path)
        papers = {paper["review_row_id"]: paper for paper in payload["papers"]}
        expected_order.extend(batch["review_row_ids"])
        for paper in payload["papers"]:
            row_id = paper["review_row_id"]
            corpus = payload.get("context", {}).get("corpus", "qss_self_hosting")
            row_meta[row_id] = {
                "batch_id": batch["batch_id"],
                "review_type": batch["review_type"],
                "corpus_or_project": corpus,
                "case_code": CASE_CODES.get(corpus, "SELF_HOSTING"),
                "audit_role": paper.get("audit_role"),
                "target_group": paper.get("target_group"),
                "machine_group": paper.get("machine_group"),
                "evidence_role": payload.get("evidence_role", {}).get("id"),
                "title": paper["title"],
                "doi": paper["doi"],
                "openalex_id": paper["openalex_id"],
            }

        batch_dir = RESULT_ROOT / batch["batch_id"]
        for expert, pattern in EXPERT_PATTERNS.items():
            files = sorted(batch_dir.glob(pattern.format(batch_id=batch["batch_id"])))
            if len(files) != 1:
                raise AssertionError(
                    f"Expected one {expert} file for {batch['batch_id']}; found {len(files)}"
                )
            path = files[0]
            response = json.loads(path.read_text(encoding="utf-8"))
            if response["batch_id"] != batch["batch_id"]:
                raise AssertionError(f"Batch ID mismatch: {path}")
            if response["reviewer"]["type"] != "AI_PROXY":
                raise AssertionError(f"Reviewer type mismatch: {path}")
            if response["reviewer"]["review_status"] != "AI_FIRST_PASS":
                raise AssertionError(f"Reviewer status mismatch: {path}")
            errors = sorted(validator.iter_errors(response), key=lambda e: list(e.path))
            if errors:
                data_quality_issues.append(
                    {
                        "batch_id": batch["batch_id"],
                        "expert": expert,
                        "type": "SCHEMA_ERROR",
                        "detail": errors[0].message,
                        "path": str(path.relative_to(ROOT)),
                    }
                )
            actual_ids = [item["review_row_id"] for item in response["reviews"]]
            if actual_ids != batch["review_row_ids"]:
                raise AssertionError(f"Row order mismatch: {path}")

            for item in response["reviews"]:
                row_id = item["review_row_id"]
                prompt_paper = papers[row_id]
                expected_paper = {
                    key: prompt_paper[key] for key in ("title", "doi", "openalex_id")
                }
                if item["paper"] != expected_paper:
                    data_quality_issues.append(
                        {
                            "batch_id": batch["batch_id"],
                            "review_row_id": row_id,
                            "expert": expert,
                            "type": "PAPER_METADATA_MISMATCH",
                            "detail": "Derived analysis restores title/DOI/OpenAlex ID from the frozen prompt. The decision is preserved.",
                            "path": str(path.relative_to(ROOT)),
                        }
                    )
                expected_group = (
                    prompt_paper["machine_group"]
                    if batch["review_type"] == "construct_audit"
                    else payload["evidence_role"]["id"]
                )
                if item["source_group"] != expected_group:
                    raise AssertionError(f"Source-group mismatch: {path} {row_id}")
                task = item["task_specific"]
                if batch["review_type"] == "construct_audit":
                    decision_map = {
                        "KEEP_IN_SCOPE": "INCLUDE",
                        "REMOVE_OUT_OF_SCOPE": "EXCLUDE",
                        "DEFER_FULL_TEXT": "DEFER",
                    }
                    if task["literature_decision"] is not None:
                        raise AssertionError(f"Unexpected literature decision: {path} {row_id}")
                    source_decision = task["scope_decision"]
                else:
                    decision_map = {
                        "KEEP": "INCLUDE",
                        "REMOVE": "EXCLUDE",
                        "DEFER": "DEFER",
                    }
                    if task["scope_decision"] is not None or task["group_decision"] is not None:
                        raise AssertionError(f"Unexpected construct decision: {path} {row_id}")
                    source_decision = task["literature_decision"]
                if item["normalized_decision"] != decision_map[source_decision]:
                    raise AssertionError(f"Decision mapping mismatch: {path} {row_id}")
                reviews[expert][row_id] = item

            source_files.append(
                {
                    "batch_id": batch["batch_id"],
                    "expert": expert,
                    "reviewer_id": response["reviewer"]["id"],
                    "path": str(path.relative_to(ROOT)),
                    "sha256": sha256(path),
                    "rows": len(response["reviews"]),
                }
            )

    for expert in EXPERT_PATTERNS:
        if list(reviews[expert]) != expected_order:
            raise AssertionError(f"Global row order mismatch for {expert}")

    consensus_rows: list[dict] = []
    for row_id in expected_order:
        decisions = [reviews[e][row_id]["normalized_decision"] for e in EXPERT_PATTERNS]
        majority_decision, majority_count = majority(decisions)
        group_values = [
            reviews[e][row_id]["task_specific"]["group_decision"]
            for e in EXPERT_PATTERNS
        ]
        group_values_present = [value for value in group_values if value is not None]
        if group_values_present:
            majority_group, majority_group_count = majority(group_values_present)
        else:
            majority_group, majority_group_count = "", 0
        meta = row_meta[row_id]
        consensus_rows.append(
            {
                "review_row_id": row_id,
                **meta,
                "claude_decision": decisions[0],
                "codex_decision": decisions[1],
                "gemini_decision": decisions[2],
                "majority_decision": majority_decision,
                "majority_count": majority_count,
                "unanimous": majority_count == 3,
                "claude_group_decision": group_values[0] or "",
                "codex_group_decision": group_values[1] or "",
                "gemini_group_decision": group_values[2] or "",
                "majority_group_decision": majority_group,
                "majority_group_count": majority_group_count,
            }
        )

    subsets = {
        "all": consensus_rows,
        "construct_audit": [
            row for row in consensus_rows if row["review_type"] == "construct_audit"
        ],
        "round0_literature": [
            row for row in consensus_rows if row["review_type"] == "round0_literature"
        ],
    }
    agreement = {}
    for name, rows in subsets.items():
        unanimous = [row for row in rows if row["unanimous"]]
        agreement[name] = {
            "rows": len(rows),
            "unanimous_rows": len(unanimous),
            "unanimous_rate": len(unanimous) / len(rows),
            "two_to_one_rows": len(rows) - len(unanimous),
            "three_way_split_rows": sum(row["majority_count"] < 2 for row in rows),
            "majority_decisions": dict(
                Counter(row["majority_decision"] for row in rows)
            ),
            "unanimous_decisions": dict(
                Counter(row["majority_decision"] for row in unanimous)
            ),
        }

    pairwise = {}
    expert_names = list(EXPERT_PATTERNS)
    for index, left in enumerate(expert_names):
        for right in expert_names[index + 1 :]:
            observed, kappa = cohen_kappa(
                [reviews[left][row]["normalized_decision"] for row in expected_order],
                [reviews[right][row]["normalized_decision"] for row in expected_order],
            )
            pairwise[f"{left}__{right}"] = {
                "exact_agreement": observed,
                "cohen_kappa": kappa,
            }

    expert_distributions = {
        expert: {
            "decisions": dict(
                Counter(reviews[expert][row]["normalized_decision"] for row in expected_order)
            ),
            "confidence": dict(
                Counter(reviews[expert][row]["confidence"] for row in expected_order)
            ),
        }
        for expert in EXPERT_PATTERNS
    }

    round0_rows = subsets["round0_literature"]
    role_summary: dict[str, Counter] = defaultdict(Counter)
    for row in round0_rows:
        role_summary[row["evidence_role"]][row["majority_decision"]] += 1

    construct_rows = subsets["construct_audit"]
    construct_by_case: dict[str, Counter] = defaultdict(Counter)
    for row in construct_rows:
        construct_by_case[row["case_code"]][row["majority_decision"]] += 1
    omitted_representatives = [
        row for row in construct_rows if row["audit_role"] == "omitted_group_representative"
    ]

    summary = {
        "schema_version": 1,
        "analysis_type": "qss_three_ai_expert_consensus",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "expert_unit": {
            "Claude": ["claude-opus-5-5"],
            "Codex": ["codex-cli-0-159-3"],
            "Gemini": sorted(
                {item["reviewer_id"] for item in source_files if item["expert"] == "Gemini"}
            ),
        },
        "source_counts": {
            "experts": 3,
            "batches_per_expert": 27,
            "rows_per_expert": 120,
            "total_decisions": 360,
        },
        "data_quality": {
            "status": "USABLE_WITH_DOCUMENTED_REPAIR",
            "issues": data_quality_issues,
            "normalization": "One Gemini row had null DOI/OpenAlex metadata despite complete frozen-prompt metadata. The derived consensus restores prompt metadata and preserves the submitted decision. Source files remain unchanged.",
        },
        "expert_distributions": expert_distributions,
        "agreement": agreement,
        "pairwise": pairwise,
        "round0_majority_keep_rows": [
            row["review_row_id"]
            for row in round0_rows
            if row["majority_decision"] == "INCLUDE"
        ],
        "round0_role_summary": {key: dict(value) for key, value in role_summary.items()},
        "construct_by_case": {
            key: dict(value) for key, value in construct_by_case.items()
        },
        "omitted_group_representatives": [
            {
                "review_row_id": row["review_row_id"],
                "case_code": row["case_code"],
                "machine_group": row["machine_group"],
                "majority_decision": row["majority_decision"],
                "majority_group_decision": row["majority_group_decision"],
                "title": row["title"],
            }
            for row in omitted_representatives
        ],
        "source_files": source_files,
    }

    majority_keep = [
        row for row in round0_rows if row["majority_decision"] == "INCLUDE"
    ]
    unanimous_keep = [row for row in majority_keep if row["unanimous"]]
    contested_keep = [row for row in majority_keep if not row["unanimous"]]

    report = f"""# การสังเคราะห์ผล QSS จากผู้เชี่ยวชาญ AI สามรายก่อนการตรวจโดยมนุษย์

วันที่วิเคราะห์: 4 ตุลาคม 2026 (Asia/Bangkok)

## สรุปสำหรับตัดสินใจ

Claude, Codex และ Gemini เห็นตรงกันทั้งหมด {agreement['all']['unanimous_rows']}/120 รายการ ({pct(agreement['all']['unanimous_rows'], 120)}) และทุกแถวมีเสียงข้างมากอย่างน้อย 2 ใน 3 ราย ไม่มีกรณีที่ทั้งสามให้คำตอบคนละแบบ เสียงข้างมากจัดเป็น INCLUDE {agreement['all']['majority_decisions'].get('INCLUDE', 0)} และ EXCLUDE {agreement['all']['majority_decisions'].get('EXCLUDE', 0)} รายการ

ความเห็นต่างกระจุกใน construct audit ซึ่งเห็นตรงกัน {agreement['construct_audit']['unanimous_rows']}/32 รายการ ({pct(agreement['construct_audit']['unanimous_rows'], 32)}) เทียบกับ Round-0 literature {agreement['round0_literature']['unanimous_rows']}/88 รายการ ({pct(agreement['round0_literature']['unanimous_rows'], 88)}) ผลนี้ชี้ว่าความไม่แน่นอนหลักอยู่ที่นิยาม scope และ evidence group มากกว่าการคัดวรรณกรรมที่อยู่นอกขอบเขตอย่างชัดเจน

ข้อสรุปการทดลองส่วนที่วัด exact-paper recovery ยังคงใช้ได้ เพราะอาศัย DOI/ชื่อเรื่องและ frozen candidate pool โดยตรง ข้อสรุปที่ระบุว่า recursion ทำให้ “กลุ่มหลักฐานจริง” หายระหว่าง exploration ต้องลดความแรงลง ผู้เชี่ยวชาญ AI พบว่า omitted-group representative 10 รายการมีเพียง 2 รายการที่เสียงข้างมากยืนยัน machine group, 3 รายการเป็น partial analogue และ 5 รายการเป็น wrong group

## ความสอดคล้องระหว่างผู้เชี่ยวชาญ

| ขอบเขต | จำนวน | เห็นตรงกัน 3/3 | แตก 2–1 | เสียงข้างมาก INCLUDE | เสียงข้างมาก EXCLUDE |
|---|---:|---:|---:|---:|---:|
| ทั้งหมด | 120 | {agreement['all']['unanimous_rows']} ({pct(agreement['all']['unanimous_rows'], 120)}) | {agreement['all']['two_to_one_rows']} | {agreement['all']['majority_decisions'].get('INCLUDE', 0)} | {agreement['all']['majority_decisions'].get('EXCLUDE', 0)} |
| Construct audit | 32 | {agreement['construct_audit']['unanimous_rows']} ({pct(agreement['construct_audit']['unanimous_rows'], 32)}) | {agreement['construct_audit']['two_to_one_rows']} | {agreement['construct_audit']['majority_decisions'].get('INCLUDE', 0)} | {agreement['construct_audit']['majority_decisions'].get('EXCLUDE', 0)} |
| Round-0 literature | 88 | {agreement['round0_literature']['unanimous_rows']} ({pct(agreement['round0_literature']['unanimous_rows'], 88)}) | {agreement['round0_literature']['two_to_one_rows']} | {agreement['round0_literature']['majority_decisions'].get('INCLUDE', 0)} | {agreement['round0_literature']['majority_decisions'].get('EXCLUDE', 0)} |

Pairwise exact agreement อยู่ระหว่าง {min(v['exact_agreement'] for v in pairwise.values()):.1%} ถึง {max(v['exact_agreement'] for v in pairwise.values()):.1%} และ Cohen’s κ อยู่ระหว่าง {min(v['cohen_kappa'] for v in pairwise.values()):.3f} ถึง {max(v['cohen_kappa'] for v in pairwise.values()):.3f} จำนวน INCLUDE ราย expert ใกล้กันมาก แต่ความต่าง 25 แถวแสดงว่า aggregate count ปิดบัง membership instability

## ผลต่อ construct และข้ออ้างของการทดลอง

| กรณี | หลักฐานจาก AI experts | ผลต่อข้อสรุปเดิม |
|---|---|---|
| C2 rainfall | ตัวแทน continuity-residual ทั้งสาม cutoff ถูก EXCLUDE และ WRONG_GROUP แบบ 3/3 บทความ Bangkok ที่ machine จัดเป็น gauge reconstruction ถูกมองว่าเป็น continuity-residual โดยตรงหรือบางส่วน | จำนวนกลุ่มที่หายอาจเกิดจาก false-positive และ cross-group misclassification จึงยังสรุปไม่ได้ว่าหลักฐาน continuity ที่อยู่ใน scope ถูก exploration มองข้ามจริง |
| C3 genome | ตัวแทน iterative-alignment ปี 2022 ถูก EXCLUDE 3/3 และ WRONG_GROUP 2/3 | การหายที่ exploration ยังเป็นจริงภายใต้ frozen machine label แต่ความหมายว่าเป็นการสูญเสียกลุ่มหลักฐานจริงยังไม่ผ่าน construct validation |
| C4 Thai legal QA | ตัวแทน defect-risk ได้ INCLUDE 2/3 แต่ได้ PARTIAL_ANALOGUE 2/3 | สนับสนุนว่ามีหลักฐานใกล้เคียงใน pool แต่ยังไม่ยืนยันกลุ่ม defect-risk แบบตรง |
| C5 vehicle | ตัวแทน vehicle make/model ถูก EXCLUDE 2/3 แม้ group label ได้ CORRECT_GROUP 2/3 ตัวแทน semantic-parts ได้ INCLUDE 2/3 และ PARTIAL_ANALOGUE 2/3 | การสูญเสีย semantic-parts มีหลักฐานรองรับมากกว่าการสูญเสีย vehicle-scope แต่ทั้งสองยังต้องให้คนตัดสิน scope และบทบาท |
| C6 education | ตัวแทน gender multi-group ถูก EXCLUDE 2/3 และ PARTIAL_ANALOGUE 2/3 | ค่า group loss ของ C6 ยังไม่ควรตีความเป็นการสูญเสียหลักฐาน multi-group ที่ผ่านการยืนยัน |
| C1 development corpus | knowledge-lens ผ่าน 3/3 ส่วน method-evidence bridge ได้ INCLUDE 2/3 แต่ WRONG_GROUP 3/3 | C1 ไม่มี frozen-group endpoint จึงไม่เปลี่ยนตารางผลหลัก แต่ยืนยันว่าการตั้งชื่อ group ยังมี error แม้ใน development corpus |

ข้อสังเกตเชิงกระบวนการเดิมยังอยู่: machine-defined group omissions เกิดก่อน final pruning ใน trace ที่บันทึกไว้ และ exact-retrieval ceilings ยังไม่เปลี่ยน การตีความเชิงสาระต้องเขียนว่าเป็น “omission under frozen automated group assignments” จนกว่ามนุษย์จะยืนยัน construct

## Round-0 literature ก่อน human review

เสียงข้างมากให้ KEEP {len(majority_keep)}/88 รายการ ({pct(len(majority_keep), 88)}) โดย {len(unanimous_keep)} รายการเห็นตรงกัน 3/3 และ {len(contested_keep)} รายการชนะ 2/3 รายการที่ KEEP ครอบคลุม 6 จาก 7 evidence roles ส่วน temporal_evaluation_observability ไม่มีรายการผ่านเสียงข้างมาก

| Row | Evidence role | คะแนนเสียง | ชื่อเรื่อง |
|---|---|---:|---|
"""
    for row in majority_keep:
        report += (
            f"| {row['review_row_id']} | `{row['evidence_role']}` | "
            f"{row['majority_count']}/3 | {row['title']} |\n"
        )

    report += """

อัตรา KEEP นี้เป็น AI-expert acceptance rate ของ review queue และยังไม่ใช่ precision เพราะไม่มี human ground truth คิว 88 รายการทำหน้าที่เป็น wide candidate set ได้ แต่ยังไม่พร้อมใช้เป็น bibliography โดยตรง

## การตีความผลทดลองหลังรวมสาม expert

1. ข้อสรุปว่า recursion ไม่มีความได้เปรียบทั่วไปยังได้รับการสนับสนุน ผล E01–E12 มีทิศทาง exact และ role ที่ผสมกัน แม้พักการตีความ group loss บางกรณีไว้ก่อน
2. Exact recovery เป็นผลที่แข็งแรงที่สุดในตอนนี้ เพราะวัด identity ที่ตรวจย้อนกลับได้ E02, E05, E07 และ E10 ยังแสดงการเพิ่มการเลือก exact papers ส่วน E03 และ E04 ยังแสดง trade-off ระหว่าง exact กับ role
3. ข้อสรุปว่า exploration เป็น control point มีสถานะเป็นผลเชิงกลไกภายใน frozen labeling system ข้อมูล trace รองรับตำแหน่งการหาย แต่ construct audit แสดงว่า label และ scope ของตัวแทนหลายกลุ่มไม่มั่นคง
4. ข้อเสนอ Core/Exploratory split ยังสมเหตุผล ผลสาม expert แสดงทั้ง membership disagreement และ acceptance rate ต่ำของ self-hosting queue จึงสนับสนุนการเก็บ Core ให้คงที่ และให้ Exploratory ผ่านการตรวจ provenance, scope และ evidence role ก่อนเลื่อนสถานะ
5. Human review มีหน้าที่เปลี่ยนสถานะข้ออ้าง ไม่ได้ทำเพียงรับรองรูปแบบ ควรตัดสิน construct ทั้ง 32 แถวก่อน แล้วจึงคำนวณ group reachability, omission และ coverage ใหม่จาก label ที่ยืนยันแล้ว

## ลำดับตรวจโดยมนุษย์

1. ตรวจ C2, C3 และ C6 ก่อน เพราะผลหลักเรื่อง group loss พึ่งพาตัวแทนที่เสียงข้างมากมองว่า out of scope หรือ wrong/partial group
2. ตรวจ construct rows ที่แตก 2–1 จำนวน 11 รายการ และแถวที่เสนอ corrected group เพราะอาจเปลี่ยน stage-localization counts
3. ตรวจ Round-0 ที่แตก 2–1 จำนวน 14 รายการ โดยเริ่มจากสามรายการที่เสียงข้างมาก KEEP ได้แก่ round0-023, round0-054 และ round0-070
4. หลัง adjudication ให้สร้าง human-confirmed mapping แล้ว rerun เฉพาะ derived construct metrics ห้าม retune policy จากผลที่เปิดแล้ว

## ขอบเขตและคุณภาพข้อมูล

การสังเคราะห์นี้ถือ Claude, Codex และ Gemini เป็นผู้เชี่ยวชาญ AI สามรายที่มีน้ำหนักเท่ากัน Gemini ใช้หลาย model ID ระหว่าง batch แต่ถูกรวมเป็น expert family เดียวตามโจทย์ พบ defect หนึ่งแถวในไฟล์ Gemini: DOI และ OpenAlex ID เป็น null ทั้งที่ frozen prompt มีค่าครบ และ schema จึงไม่ผ่าน การวิเคราะห์ใช้ metadata จาก frozen prompt สำหรับแถวดังกล่าวโดยคงคำตัดสิน DEFER เดิมไว้ ไฟล์ต้นทางทุกไฟล์ไม่ถูกแก้

ผลจากผู้เชี่ยวชาญ AI ยังไม่ใช่ human validation, independent external validation หรือ adjudication ข้อสรุปเกี่ยวกับ construct validity จึงมีสถานะ provisional

## แหล่งข้อมูล

- `outputs/qss-ai-proxy-review/MANIFEST.json`
- `outputs/qss-review-results/*/*.json*`
- `outputs/qss-stage-diagnostics/REPORT.md`
- `outputs/qss-parameter-sensitivity-v1/RESULTS.md`
- `docs/qss_recursive_curation_failure_modes_manuscript_en.tex`
"""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (OUTPUT_DIR / "consensus_rows.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(consensus_rows[0]))
        writer.writeheader()
        writer.writerows(consensus_rows)
    (OUTPUT_DIR / "REPORT.md").write_text(report, encoding="utf-8")
    inventory = {
        "generated_at": summary["generated_at"],
        "source_files": source_files,
        "artifacts": [
            {
                "path": str((OUTPUT_DIR / name).relative_to(ROOT)),
                "sha256": sha256(OUTPUT_DIR / name),
            }
            for name in ("SUMMARY.json", "consensus_rows.csv", "REPORT.md")
        ],
    }
    (OUTPUT_DIR / "INVENTORY.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Wrote {OUTPUT_DIR.relative_to(ROOT)}")
    print(
        f"Consensus: {agreement['all']['unanimous_rows']}/120 unanimous; "
        f"Round-0 majority KEEP {len(majority_keep)}/88"
    )
    print(f"Data-quality issues: {len(data_quality_issues)}")


if __name__ == "__main__":
    main()
