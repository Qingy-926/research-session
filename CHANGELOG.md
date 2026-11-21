# Changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). No release artifact has been
published yet; the entries below describe the source tree only.

## [Unreleased]

### Planned

- Online CI execution, clean-host UI loading, and explicitly authorized live-provider checks.

## [5.2.0] - 2026-11-21

### Added

- Opt-in `first_review` contract for isolated candidates: an enumerated key-claim list, fixed
  required checks, conclusion-to-source traceback, same-root-cause sweep, and required
  location/actual/expected/impact/minimal-fix/acceptance fields on every blocker.
- Blocker closure routing `local` versus `recheck`. `local` is limited to exact wording edits in
  bound `.md`/`.txt` reports with unique, non-overlapping `before`/`after` text; everything else
  requires a remaining specific judgment (`recheck`).
- Offline `audit_dispatch.py --verify-local-edits <receipt> --expected-receipt-sha256 <value>`:
  reconstructs the reviewed bytes, rejects duplicates, omissions, extra edits and hash drift, and
  reports `locally_verified` with `gate_passed=false`.
- Recovery contract statuses `local_verification_required`, `locally_verified` and
  `recheck_required`; regression coverage in `test_first_review_candidate.py`.

### Changed

- Review prompts and the candidate response contract require per-row reasons, `finding_ids` and
  `missing_evidence`; a mixed remediation round re-reviews only remaining judgments and affected
  dependencies while accounting for every old blocker.
- Rules digest updated. Registered 5.1.0 receipts remain read-only and require original evidence
  revalidation; three scientific gates, authorization, `xhigh` effort, send locks, original
  verdicts/blockers and the two-round ceiling per `review_id` are unchanged.

### Unverified

- Real-provider first review, clean-host UI loading, online CI and public publication are not
  verified in this release. The only candidate evidence is one synthetic contrast; no real-task
  quality gain is established.

## [5.1.0] - 2026-11-20

### Added

- Gap-driven retrieval and full-text evidence workflow with bounded searches, citation
  traversal when available, counterevidence, version deduplication and explicit stopping limits.
- Claim-support review separating source facts, author interpretation, model inference and
  hypotheses; comparisons retain protocol differences and conclusions receive a scope check.
- Optional offline `evidence_context.py` reusing sources JSON with locations/access levels,
  exact-quote and optional source-hash checks, duplicate-excerpt detection and a visible budget limit.
- A real-paper four-condition pilot on two held-out questions, with independent model-assisted
  blind review and source spot-checks. Judgment scores tied; retrieval arms consulted one
  additional required vision source. No general research-quality gain is established.

### Changed

- Entry, paper-reading, novelty/baseline and reviewer skills route to the shared evidence workflows.
- New workflow references enter the rules digest. Registered prior receipts remain read-only
  and require original evidence revalidation; gates, authorization and review rounds are unchanged.

## [5.0.0] - 2026-11-20

### Added

- Dual-host manifests (`.trae-plugin/plugin.json`, `.codex-plugin/plugin.json`) sharing one
  `skills/`, `scripts/`, `contracts/`, and `assets/` tree.
- Versioned v1 contracts for provider/adapter configuration, redacted run manifests, and
  recovery/error semantics (`contracts/`).
- Opt-in redacted run manifests: disabled by default, enabled only by
  `RESEARCH_SESSION_RUN_MANIFEST_DIR`.
- Offline citation and numeric-claim guard (`claim_citation_guard.py`): `[[cite:ID]]` and
  `[[result:ID]]` binding, verified-source and quote checks, and an optional search-limitation
  note check.
- Offline golden integrity evaluations for fabricated citations, writing without evidence,
  missing providers, degraded search, and reviewer replies without invented numbers.
- Public documentation: installation and configuration, capability matrix, contribution,
  security, third-party notices, release and rollback procedure, and issue/PR templates.
- Offline Windows/Ubuntu CI workflow and deterministic release packaging with checksums.

### Fixed

- Read-only preflight emits opt-in run manifests on both ready and refused normal returns.
- HTTPS responses obey the total request deadline, including slow headers and body reads.
- Read-only preflight recognizes the registered legacy route shape without verifying a live
  route, inheriting approval, or changing dispatch permissions.

### Changed

- Provider, model, credentials, and project paths are explicit configuration. There are no
  implicit defaults; a missing endpoint or model returns `provider_not_configured` without
  sending a request.
- The rules digest changed with this increment. Continuation semantics: an unknown digest
  refuses continuation, only registered compatible digests may continue, and original
  evidence is always revalidated. Compatibility never inherits a previous pass or resets
  review rounds.

### Removed

- Private machine defaults: the local gateway default, the private wire model, private route
  files, and private project paths.
- The `mentor-method-distillation` skill; routing now uses `research-mentor`.
- Backup scripts and `.bak` files from the private maintenance line.

### Breaking

- Configurations that relied on implicit private defaults must now set the provider endpoint,
  model, and credential environment variable explicitly.
- The removed `mentor-method-distillation` entry no longer exists; use `research-mentor`.

### Baseline

- Public 5.0.0 is derived from the private 4.0.5 maintenance line. 4.0.3 is an earlier
  historical baseline recorded in the audit evidence; it is not the direct parent of this tree.

## Earlier history

Versions before 5.0.0 were private maintenance snapshots and are not published here.
