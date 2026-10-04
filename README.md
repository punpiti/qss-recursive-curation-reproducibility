# QSS Reproducibility Deposit

Supports: "Recursive bibliographic curation: failure modes, candidate-pool
sensitivity, and evaluator-specific construct audits" (QSS submission).
Built offline and deterministically from the authors' working repository.

## Contents

| Material | Location |
|---|---|
| Case-coded project bibliographies (C1--C7), abstracts removed | `data/qss-public-release/` |
| Frozen policy, corpus, group-definition, and sensitivity configurations | `config/` |
| Frozen OpenAlex candidate pools with request manifests | `outputs/qss-temporal-candidate-pools-citing*/`, `outputs/qss-multicorpus/*/candidates/` |
| Seed-scope audits and the C2 seed-expansion record | `outputs/qss-multicorpus/*/` |
| Per-bag results and candidate-level selection traces | `outputs/qss-late-pruner-v2-*/`, `outputs/qss-multicorpus/*/evaluation/` |
| Stage diagnostics, construct-audit and self-hosting review queues | `outputs/qss-stage-diagnostics/`, `outputs/qss-self-hosting/` |
| Parameter and candidate-pool sensitivity runs (every perturbation) | `outputs/qss-*-sensitivity-*/` |
| Evaluator prompts, response schema, raw Claude/Codex/Gemini responses with rationales, agreement analysis | `outputs/qss-ai-proxy-review/`, `outputs/qss-review-results/`, `outputs/qss-ai-expert-synthesis-2026-10-04/`, `config/qss_review_response.schema.json` |
| Analysis code and figure builders | `scripts/`, `scholarly_radar/openalex.py` |

## Withheld abstracts

Abstract text is not redistributed. Every withheld abstract is replaced by
`abstract_sha256` and `abstract_chars` (JSON and CSV) or by
`<withheld; sha256=...; chars=...>` (evaluator prompts). Run

    python scripts/rehydrate_qss_abstracts.py <pool.json> ... --output-dir rehydrated/

to re-fetch abstracts from OpenAlex and accept only digest-matching text.
OpenAlex records can change after the 2026 retrieval date; mismatches are
reported, and affected records cannot be replayed exactly.

## Other limits

- Seed-bibliography abstracts are not included.
  `data/qss-public-release/seed_abstract_digests.csv` gives the digest and
  length of each source abstract by public BibTeX key. `source_verified` is true
  for C2--C6, whose source files match the frozen SHA-256. The C1 source has
  since gained two records and its frozen version was not retained, so C1
  digests describe the current source and may differ from the text used.
  Records without a digest had no abstract in the source.
- Source-manuscript titles and project documents remain confidential. Corpus
  identifiers in paths, fields, and code use the manuscript case codes C1--C7.
- The C7 diagnostic case stopped before retrieval and has no evaluation inputs.
- Local filesystem paths are redacted to `<REDACTED_LOCAL_PATH>/<file name>`.

## License

Code (`scripts/`, `scholarly_radar/`) is released under the MIT License. Data,
configurations, and documentation are released under CC BY 4.0. Bibliographic
metadata derived from OpenAlex is CC0 at source.

`release_manifest.json` records SHA-256 checksums for every bundled file.
