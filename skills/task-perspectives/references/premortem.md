# Premortem: rehearse project failure

## Original user prompt

> 假设这个项目已经失败，请倒推最可能的失败原因。每项给出：最早信号、预防措施、触发条件和补救方案，并按风险优先级排序。

## Application

Use when explicitly requested, or when an uncertain dependency or untested assumption could cause costly rework, missed delivery, or invalid conclusions. Reuse existing risk records for an unchanged plan. Ordinary low-impact tasks do not require a risk table.

Start from the current objective, deliverable and acceptance evidence. Assume the agreed delivery has failed, then identify plausible causes grounded in available artifacts. Label each as an observed weakness or a hypothesis; do not invent likelihoods, thresholds, or past failures.

Rank by impact, supporting evidence for likelihood, and the time left to detect or recover. Explain critical ordering judgments in plain language; do not manufacture numerical risk scores.

For each material cause provide:

| Field | Required content |
|---|---|
| Failure cause | The concrete way the objective or acceptance could fail |
| Earliest signal | An observable artifact, check, event or missing dependency |
| Prevention | A specific action before the signal becomes a failure |
| Trigger condition | The observable condition that starts the response; label a proposed threshold as a proposal |
| Recovery | An actionable response after the trigger, including any required new authorization |

Finish with changes that should be made to the active task or acceptance now. Carry out already authorized checks and ordinary repairs; reserve only consequential unresolved tradeoffs for the user. Stop when further items would not change the plan. Keep the result in the existing task/spec or response, not a new recurring risk register.

For example, when editable deliverables depend on a font or renderer not yet available, the early signal is a failed minimal render, prevention is checking that small sample early, the trigger is failure before layout work, and recovery is choosing an authorized supported rendering route. A missing render is not proof that the final artifact displays correctly.

This is main-model risk analysis, not independent review or a guarantee. It neither changes frozen research criteria nor grants retries, spending, external actions or protected-state access.
