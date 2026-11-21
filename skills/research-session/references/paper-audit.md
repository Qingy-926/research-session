# Paper audit template (reproduction ladder L0)

Reproduction begins with **auditing the paper**, not coding. Answer: (1) what numerical claims and conditions does it make, (2) how is the protocol defined and what is missing, and (3) how are ambiguities resolved? Do not enter L1 protocol specification until the audit is complete.

Keep one audit package per project in `01-资料/` or the project's customary location. The three sections cross-reference each other.

## Claimed numbers table

| # | Metric | Paper value | Original location (table/figure/line) | Conditions (dataset/protocol/seed/operating point) | Our comparison target | Threat most likely to defeat claim | Verdict | Notes |
|---|---|---|---|---|---|---|---|---|

- A number absent from the table must not appear in criteria or comparison claims.
- The comparison target defaults to the paper value. If independent verification (third-party reproduction or literature comparison) shows it is unattainable, changing the target requires **new preregistration and review point 1**; never silently change it.
- Record distinct definitions of a nominally identical metric on separate rows.
- Apply both gates below to each row; verdict is `verified`, `fragile`, or `refuted`.

## Two-gate check (recalculation is not verification)

The claim-verify mechanism from gaasher/Agent-Loop-Skills (MIT) emphasizes that reproducible numbers are not automatically credible; erroneous findings can also be reproducible.

1. **Recalculation:** precisely recompute with our code/data under the same definition. Failure → `refuted`, triggering cross-check and challenge to the comparison target.
2. **Stress test:** select the most likely threat for the claim type:

   | Claim | Default threat |
   |---|---|
   | Difference | Does direction hold under an alternate definition or operating point? |
   | Correlation | Remove one subject/point; does a single observation drive the effect? |
   | Causal | Confounding and Simpson's reversal. |
   | Subgroup/rate | Is subgroup sample size sufficient, or is small n noisy? |
   | General | Outlier sensitivity. |

   Check one claim at a time. Every verdict requires an actual recalculation or rerun; a cursory visual impression is insufficient.
3. **Disposition:** `verified` means both gates passed. `fragile` means the value is correct but inference unstable: record one of hedge, scope, or retract in the row; do not retain the claim unchanged. `refuted` means recalculation failed; a change in target requires new preregistration and review point 1.

## Common numerical errors

Check inconsistent rounding across abstract/text/tables; old experiment values left in text after final tables change; percentage vs. absolute count confusion; and off-by-one subject/record/beat counts at cohort boundaries.

## Protocol extraction

For every item mark `[论文明确]` (explicit) or `[论文未明]` (not specified); move unclear items to the ambiguity list.

| Item | Quoted paper content | Status |
|---|---|---|
| Dataset and version | | |
| Cohort and subject split | | |
| Training/test beat or sample split | | |
| Preprocessing and features | | |
| Model and training settings | | |
| Evaluation protocol (gallery/probe, decision rule, operating point) | | |
| Metric definitions | | |
| Randomness (seed counts and repeats) | | |

## Ambiguity register

| # | Question | Paper quotation | Interpretations | Resolution | Basis | Impact |
|---|---|---|---|---|---|---|

Resolve in order: (1) the original paper, including other sections; (2) reference implementation, distinguishing author code from third-party reproduction; (3) independent literature on comparable practice; (4) if still insufficient, present options to the user rather than guessing. Record whether each resolution affects criteria, protocol, or only documentation.

## Cross-checking

- Compare key claimed values with independent literature or public reproductions of the same magnitude. Note substantial discrepancies in the audit package as evidence for whether to change the comparison target.
- Changing the target changes criteria and requires preregistration and review point 1, never mid-execution substitution.
- Trace cited numbers to the cited paper's actual table/figure/line. If unverifiable, downgrade all our statements to qualitative description and register the gap; do not use it as that paper's measured value.

## Five audit passes

Separate large audits by dimension and produce findings under HIGH (affects criteria/target), MEDIUM (interpretation), and LOW (record), so review packages can prioritize:

| Dimension | Check |
|---|---|
| Numerical | Claim table, two gates, common error checklist |
| Terminology | Whether a concept changes names and our usage is consistent |
| Code-paper alignment | Compare official/reference behavior with paper, following fidelity rules |
| Citations | Trace secondary numbers and check support |
| Evaluation completeness | Operating points, decision rules, gallery/probe definitions complete and consistent |

## L0 pass

Each claim row has source location, conditions, and a defined target; every number has a two-gate verdict with no unchanged fragile claim; protocol items are all marked explicit/unspecified; all ambiguities have resolution and basis or are listed for the user; key numerical cross-checks including secondary-source tracing are documented. Until cleared, stop at L0 fail-closed and report missing items.
