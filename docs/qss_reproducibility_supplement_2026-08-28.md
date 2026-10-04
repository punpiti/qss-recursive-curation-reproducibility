# QSS Reproducibility Supplement — Retrieval, Cache, and Release Provenance

## Evaluation Boundary

- Candidate retrieval and evaluation are separate phases. Every reported model evaluation is cache-only and made zero OpenAlex requests.
- All 12 corpus/cutoff cases use saved candidate pools, frozen evidence-group configurations, a frozen v2 policy, and 150 deterministic stratified bags.
- The offline diagnostic runner replays the deterministic fixed baseline and reconstructs recursive/late selections from saved audit records. Derived per-positive exact counts reconcile exactly to the saved policy aggregates.

## Retrieval and Cache Provenance

- Retrieval was bounded and cached, and candidates without abstracts were removed before evaluation.
- The anonymous OpenAlex daily request allowance was exhausted during C4 retrieval. Fifteen remaining requests were made through the explicitly authorized `urban.cpe.ku.ac.th` SSH-curl egress and written into the same local cache. This operational change affected transport, not scoring, groups, thresholds, or evaluation.
- The self-hosting Round 0 request journal contains eight attempts, seven successful responses, and seven unique concept-only queries. A reproducibility replay used seven cache hits and zero network requests.
- Request-level records are retained in corpus request manifests and OpenAlex JSONL journals. Public copies must replace machine-local absolute paths with logical project paths.

## Seed Resolution and Expansion Provenance

- AMT and C4 each retain one audited unresolved seed. At the maximum cutoff, resolved historical-seed coverage is 97.62% and 96.88%, respectively; C6 resolves 88.89%, and genome resolves all historical seeds. Each exceeds the frozen 80% completeness floor where applicable.
- AMT uses a separate scope-conditioned evaluation bibliography with three reviewed additions no later than the earliest cutoff. The canonical AMT BibTeX is unchanged, and the additions are not counted as system discoveries.
- External project bibliographies are local research sources and are hashed for verification only. They are not automatically part of a public replication bundle.

## Publication Artifact Inventory

- `outputs/qss-reproducibility-manifest/artifact_manifest.json` and `.csv` inventory 100 frozen or derived artifacts with SHA-256, byte size, schema/format identifier, and release status.
- Project-relative evaluation/configuration artifacts are marked as public release candidates pending final QA.
- Five external BibTeX sources are marked private and not for redistribution without permission.
- Thirteen request manifests require path sanitization.
- Sixteen candidate, retrieval-log, or human-review artifacts require a scholarly-metadata and reconstructed-abstract redistribution review.

## Human-Review Workbook Experiment Stage

- Workbook generation is a registered derived stage named `human_review_instrumentation`, not a manual post-processing step.
- `config/qss_review_workbook_experiment.json` declares the frozen input hashes, stage order, offline-only policy, workbook schema, expected row counts, and output paths.
- `scripts/run_qss_review_workbook_pipeline.py` runs construct adjudication, boundary-scope preparation, literature adjudication, aggregate inventory, and contract validation in dependency order. A failed input hash or validation gate stops the stage.
- Every workbook uses the `qss-paper-review-v1` contract. The pipeline rebuilds all workbooks twice and requires identical SHA-256 values before writing `outputs/qss-review-workbook-experiment/RUN_MANIFEST.json`.
- An ordinary working build runs one pass and writes `BUILD_MANIFEST.json`; use `--verify-determinism` only when freezing or releasing an evidence version.
- The workbooks are research instruments for L2 adjudication. They do not change the frozen machine evaluation; completed human decisions create a later, explicitly versioned evidence state.

## Reproduction Commands

Run in the micromamba environment `ml`:

```bash
micromamba run -n ml python scripts/derive_qss_stage_diagnostics.py
micromamba run -n ml python scripts/run_qss_review_workbook_pipeline.py --verify-determinism
micromamba run -n ml python scripts/build_qss_reproducibility_manifest.py
micromamba run -n ml python scripts/build_qss_public_release.py
micromamba run -n ml python scripts/validate_qss_manuscript_consistency.py
micromamba run -n ml python -m unittest discover -s tests
```

The public-release builder creates a local, sanitized release candidate and ZIP; publishing it externally still requires repository/archival approval. Rebuilding a hash manifest after changing a frozen artifact creates a new evidence version; it must not be presented as identical to the current frozen inventory.
