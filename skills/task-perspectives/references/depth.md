# Depth: evidence, methods, and validation

## Original user prompt

> 我想要这个任务更深入的版本，而不是更大的版本。在重写之前请说明：1、顶尖专家会做哪些我没要求的事；2、他们会从哪些最原始的素材入手（而不是我整理好的）；3、他们使用的具体方法和工具；4、除了你我之外，还有什么能证明结果是对的。然后请修改我的提示词

## Adapted prompt

```text
I need to complete [task]. Increase professional depth without expanding its scale.
Using testable standards for this field, explain:
1. Additional checks or reasoning necessary for this task to hold, distinguishing necessary from optional.
2. Primary sources, raw data, actual code, or original records to consult; reuse reliable existing material without repeating collection or exceeding access authorization.
3. Concrete methods and tools, why they fit, what they can answer, and their limits.
4. Reproducible calculations, independent evidence, controls, tests, or external standards for validation, including what would falsify the conclusion.
Distinguish verified, feasible to verify, and currently unverifiable claims. Expert language is not evidence.
If asked to improve the prompt, rewrite it; if asked to deliver an artifact, apply these standards directly.
```

## Check implementation completeness when needed

When requirements, plans, deliverables, or evidence risk omissions or inconsistency, use existing materials; do not require new specs, task files, or a complete matrix.

1. Before implementation compare current valid requirements and constraints with plans, tasks, dependencies, and acceptance; identify omissions, conflicts, ambiguities, and tasks without corresponding requirements.
2. Afterwards check actual artifacts, behavior, validation, and completion claims. Distinguish satisfied, partial, missing, conflicting, and insufficient evidence, with concrete support. Checkmarks and model opinions are not actual validation.
3. Fix gaps within existing authorization and retest. For unauthorized scope, major tradeoffs, or missing key evidence, retain the gap and pause only dependent work under current rules. Record continuation needs in the existing project entry, not a parallel status source.

Scale depth to risk and materials; verify directly affected content for simple tasks. Research still follows current protocols, evidence requirements, and stricter gates; this does not authorize additional experiments or replace independent review.

## Applicability and exit

Use when the user requests deeper rather than larger work, increased rigor, primary-evidence review, methodology, or validation design. Ordinary questions, local formatting, and polishing do not require all four checks.

Translate expert practice into an explainable method for this task, not invented authority. If primary materials are absent, first search authorized existing sources, then state effects on conclusions. Do not assume authorization for extra collection, paid calls, new experiments, or sensitive access. External validation provides checkable evidence, not a guarantee.
