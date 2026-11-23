# Changelog

All notable changes to this project are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). No release artifact has been
published yet; the entries below describe the source tree only.

## [Unreleased]

### Planned

- Online CI execution, clean-host UI loading, and explicitly authorized live-provider checks.

## [6.4.0] - 2026-11-22

- Allow an explicitly approved continuation of a task, including after expiry. Verify
  linked approvals and historical receipt windows before sending, and retain the
  original count start, cumulative request limit, review scope, and receipt chain.
- Check the send time and closeout reserve before dispatch. When recording a response,
  recheck the active approval window and evidence, and preserve fractional seconds
  so receipts remain verifiable when approval and response occur in the same second.

## [6.3.0] - 2026-11-22

- Replacement-style simplification: one home per rule. The three modes are defined once in
  `SKILL.md`; review-contract semantics live in `independent-review.md`; pack format and
  per-point checklists stay in `codex-review-pack.md`.
- Compressed recovery, format-correction and new-cycle prose into lookup sections. No
  script behavior, contract field, review gate or send-lock semantics changed.
- Aligned the repeated-failure stop with the host rule (two failures without new evidence)
  and added the exploratory-analysis exception to `project-work.md`.
- Removed dangling references to the retired `academic-report-writing` skill.

## [6.2.0] - 2026-11-22

- Clarify that autonomous and unattended progress has no plugin-level fixed round or
  request count. Only the task approval's `max_requests`, `reserved_requests`,
  `deadline` and `closeout_seconds`, plus real progress and safe stops, bound the work.
- Treat numbers such as 6 as concrete approval values, not defaults. The two-round
  limit remains specific to the developer-controlled mode.
- Add `session_guard.py`, a stdlib-only in-session state machine that binds
  `research-session/session-plan/v1` to `research-session/session-approval/v1`,
  checks dependencies, deadlines and budgets, and fails closed with exit 2. It never
  executes actions, uses the network or starts background processes.
- Keep extension authorization on the same task approval and evidence directory
  instead of opening a new project/verification tree per time budget.
- Add guard, budget-value (6 and 12) and rejection coverage.

## [6.1.1] - 2026-11-22

- Filter task budget history by ledger time before opening receipts. Legacy sends
  registered within the task window still use their verified receipt timestamp.
- Keep the existing evidence checks for in-window fixture exclusions and the
  current review object. Existing ledgers and receipts need no migration.

## [6.1.0] - 2026-11-22

- Add task-bound continuous remediation for autonomous and unattended session work.
- Keep the same review ID, point, cycle and cumulative round; no new-cycle approval within the approved task.
- Require changed controlled evidence, fresh successful verification and an active blocker before each continuation.
- Atomically count task request allocations across project review sends; preserve locks, unknown-send stops and closeout time.
- Keep the controlled two-round path, scientific gates, immutable receipts and fixture-only background controller.

## [6.0.1] - 2026-11-21

- Add explicit append-only, revocable recovery for independently verified native consumer fixtures.
- Bind producer source, retained execution log, exact ledger entry and original/preserved bytes.
- Preserve real send history and scientific gates; revalidate provenance during scans and claims.
- Retain desktop preferences, original receipts, bounded format correction and ordinary round limits.

## [6.0.0] - 2026-11-21

### Added

- Codex++ Research Session 0.3.0 can send per-chat controlled or autonomous preferences.
  The unattended plan request carries required minutes and an optional CNY estimate.
- The entry skill uses the current request to route plan preparation and scopes optional work
  to the plan's evidence, time and approvals. Real unattended dispatch remains fixture-only.
- Explicit compatibility for the exact 5.3.0 rules digest. Existing receipts require current
  evidence checks, and corrected baselines still require their original provenance.

## [5.3.0] - 2026-11-21

### Added

- Explicit `review_format.py` format-only correction for a published first-round adverse report
  whose contract failed. Requires the original receipt digest, exact provider/model/credential
  route, known rules compatibility, a complete ledger scan and a single persistent claim shared
  across project copies. It performs at most one HTTP call and never retries unknown outcomes.
- Immutable correction sidecars bind the request, shared claim, saved raw response and terminal
  status. Every finding, decision, reason, evidence and quantitative value is preserved with
  JSON type-sensitive comparison. Only coverage status and finding links may be corrected;
  locator arrays are losslessly retained as context.
- Ordinary `--previous ... --corrected-previous ...` rechecks validate the complete correction
  provenance and bind all artifacts. The original round0 receipt stays unchanged; the adverse
  baseline counts as effective round1 and a valid ordinary recheck remains round2.

### Unverified

- Real-provider correction, clean-host UI loading, Ubuntu regression and online CI are not
  established by the offline fixtures. No scientific approval or execution authority is added.

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
