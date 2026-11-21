# Reproduction ladder (L0–L6) and metric definitions

## Purpose

Reproduction failures often result from skipping levels: coding before paper audit, training before data verification, or claiming improvement without measuring noise. This ladder has seven stages with explicit pass conditions; do not advance before the previous stage passes.

Use it to plan and locate the current level for a new reproduction. For factor investigation or supplementary experiments, check required levels first; e.g. changing preprocessing changes protocol and requires an L1 amendment and renewed L3 anchor. Preregistration criteria and six parts are in [preregistration](preregistration.md); this file governs progression and pass evidence.

## Ladder

| Level | Name | Work | Pass condition fixed before results | Artifact |
|---|---|---|---|---|
| L0 | Paper audit | Produce the three-part `references/paper-audit.md` package | Each claimed number locates original text; protocol extracted item by item as explicit/unspecified; all ambiguities resolved from evidence, without guessing. | Paper audit package |
| L1 | Protocol specification | Turn audited protocol into machine-checkable specification, metric register, and preregistration | Formula precedes each metric; data/cohort/split/seed frozen; preregistration passes review point 1. | Protocol and preregistration |
| L2 | Data pipeline | Compare raw hashes with official manifest; check cohort and data boundaries including record overlap, subject pools, and leakage | Each hash matches, cohort matches spec, and boundary audit concludes explicitly, even if accepting optimistic aspects of the paper protocol. | Data validation record |
| L3 | Anchor reproduction | Freeze code and reproduce one known reference implementation or literature number | Match digit by digit with tolerance fixed in advance; investigate code/protocol mismatches before progressing. | Anchor comparison |
| L3.5 | Pre-training validator | Model existence → forward shape → parameter count within ±5% of paper → gradient check → 1-epoch real-data smoke → known pitfalls checklist | All pass before formal training; repair any failure and rerun the chain. | Validator record |
| L4 | One complete combination | Under preregistration run training → evaluation → interpretation for one combination | L3.5 passed; complete artifacts, fail-closed marker written only after rc=0, and interpretation passes. | Single-combination artifacts |
| L5 | Expand matrix | Run N combinations of seeds/configurations per preregistration | All artifacts, summary JSON, and criteria fixed in advance. | Matrix summary |
| L6 | Attribution and verdict | Close or confirm each factor and produce final attribution | Every factor registered; do not reopen a falsified factor without new preregistration and point 1. | Verdict and ledger entry |

## Stage → audit skill

Load the matching skill when the stage actually needs its evidence checks; the ladder remains the single route, no separate reproduction entry skill is used.

| Stage | Audit skill | When to load |
|---|---|---|
| L0 Paper audit | `paper-source-audit` | Aligning paper text, appendix, official code and implementation, or when a reproduction lacks locatable primary-source evidence |
| L1 Protocol and preregistration | `reproduction-preflight` | Freezing data, code, protocol and input hashes before execution |
| L2 Data pipeline / L3.5 pre-training validator | `reproduction-preflight` | Checking manifest, cohort, leakage, environment, tests and resource evidence |
| L3 Anchor / L4 single combination | `research-code-audit` | Tracing the causal chain from data and preprocessing through training, selection and evaluation |
| L5 Expand matrix | `experiment-matrix-audit` | Checking completeness, missing or duplicate cells across methods and random axes |
| L6 Attribution and verdict | `metric-verdict-audit` | Recomputing from raw JSON and issuing the verdict against frozen criteria |
| Cross-cutting logs and snapshots | `research-log-governance` | Auditing, summarizing, encoding-repairing or archiving logs and code snapshots |

Typical consequences of skipping: without L0 the target may be a paper claim rather than a reproducible value; without L2 leakage or cohort differences contaminate later numbers; without L3 even severe training-code errors lack an alarm. These are pitfalls observed in real reproduction work.

## Fidelity when official code exists

- When author code or a verified reference implementation exists, port its logic rather than rewriting merely by reference.
- Do not replace libraries, formulas, or defaults with assumed equivalents unless ambiguity was resolved and recorded.
- Investigate or report every discrepancy, however small, rather than guessing. Paper-audit resolution priority is original text → official code → independent literature → user.

## Incidents and promotion

Register each protocol accident, data-definition error, or script defect within the project with observation, root cause, action, and evidence path. Promote generalizable patterns to the project pre-reproduction live checklist, mandatory at L0 and L3.5; the project checklist comes first, workspace experience supports cross-project reuse.

## Metric register

1. Register each metric before first use; criteria may contain only registered metrics.
2. Give different names to distinct definitions, such as closed-set identification, open-set paper line, and deployment line. A deployment operating point with a different definition must not be called EER.
3. Freeze definitions after registration. To change one, create a new row and register an amendment; do not edit the old row.

| Metric | Exact definition/formula | Dataset | Protocol | Seed structure | Must not call | Registration |
|---|---|---|---|---|---|---|
| Example open-set paper line | Under rule R, error = 1 − TPR@FPR=… (write formula exactly) | PTB 235 people | Paper random beats | 5 subject_seed × 10 beat_seed | Do not mix with deployment line | Preregistration §x / ledger Axx |

## Noise floor / 噪声底 and resolution budget (two gates before improvement)

Before measuring improvement, determine what difference the evaluator can resolve and whether the design is valid. Both gates must pass before criteria design or progression; use a budget against endless patching (mechanism from gaasher/Agent-Loop-Skills power-analysis and optimize-loop).

1. Measure existing results for ≥3 seeds under identical configuration and criteria, or run three additional groups; metric range or half-width of 95% CI is the noise floor.
2. **Resolution gate:** expected effect must be greater than the noise floor; otherwise stable passage is statistically near impossible. Use paired same-seed design to remove subject-pool effects, add seeds, or admit that scale cannot be measured.
3. **Validity gate:** clear the defect checklist, recording controlled/not applicable/uncontrolled for confounding, selection bias, multiplicity, optional stopping, prespecified outcomes, and measurement validity.
4. **Budget:** at most eight design/seed iteration suggestions; if gaps persist beyond budget, report and stop at this level.
5. Record noise floor, provenance, and both gate decisions in preregistration §1 motivation and §4 criteria rationale.
6. If an n=1 change is within baseline's same-structure range, mark HOLD only (cost-control gate), not a conclusion. Conclusions come only from fixed formal criteria.

## Minimal effect of interest

Preregister the smallest worthwhile improvement for each level/preregistration and use it to determine required seed/pair counts. Design for the minimal effect of interest, not an optimistic larger effect that would be undetectable under realistic conditions. One project's evaluator struggled below ~3 percentage points; this was that project's lower bound for worth-doing judgments, not a universal threshold.

## Margin in keep criteria

Improvement must clearly exceed the noise floor to claim improvement, advance, or keep. Within noise, register “noise-level, do not keep”; do not call it slight improvement. For one project the open-set same-configuration range was ~1.1–1.2 percentage points; smaller changes were not evidence, and subsequent gates used this as that project's compatibility lower bound.
