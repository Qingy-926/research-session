# Contributing

Thanks for improving research-session. This project is small on purpose: skills plus
standard-library scripts, no runtime dependencies, no telemetry.

## Before you start

- Open an issue (or use the local templates in `.github/ISSUE_TEMPLATE/` while the repository
  is not published) before large changes.
- Keep changes scoped. Do not rewrite review contracts, scientific gates, or ledger semantics
  to make code simpler.
- Do not add third-party runtime dependencies. Tests may use pytest only.

## Run the tests

From the repository root, offline:

```bash
python -m pytest tests skills/research-session/tests -p no:cacheprovider -q -ra
```

Documentation-only checks:

```bash
python -m pytest tests/test_docs_consistency.py -p no:cacheprovider -q
```

Tests must not access the network, the host caches, or real project ledgers. Skips are
acceptable only with an explicit reason (for example, host loading or a live provider).

`tests/conftest.py` blocks non-loopback connections in every test unless
`RESEARCH_SESSION_ALLOW_LIVE=1` is set. Live tests must be marked `live` and require both
that variable and an explicit provider configuration; they stay skipped by default. The
manual `.github/workflows/live-provider.yml` job is unverified until it is run.

## Pre-submit checklist

- [ ] `pytest` passes locally; no new failure and no unexplained skip.
- [ ] Both `plugin.json` files still agree on `version`, `license`, `repository`, and metadata.
- [ ] `README.md` capability matrix and skill links still match the `skills/` tree.
- [ ] `CHANGELOG.md` has an entry for the version being changed.
- [ ] No private paths, private project names, credentials, or real ledger data in the diff.
- [ ] New behavior has a regression test; bug fixes include a test that fails before the fix.

## Pull request expectations

- One logical change per pull request; explain what changed and why.
- Link the issue and the evidence (test output or a small reproduction).
- Keep commit titles imperative and scoped, for example `docs: add release rollback steps`.
- Reviewers may ask for a smaller diff or for a failing test first. That is expected.
- Do not include generated caches, virtual environments, logs, or backup files.

## Scientific gates that must not be weakened

Any contribution must preserve:

- The three independent review points (before execution, before registration, before final
  reporting).
- Evidence binding and input hashes; `review_completed` stays separate from `gate_passed`.
- The send lock and the rule that ambiguous failures are never automatically resent.
- The two-round ceiling per `review_id`; a new cycle requires an explicit user decision.
- Budget, authorization, and data boundaries; passing a gate grants no new permission.
- Refusal of unknown rule digests; compatibility must be registered and evidence revalidated.

## Adding a skill

1. Create `skills/<skill-name>/SKILL.md` with `name` and `description` front matter; the
   directory name must equal the front-matter `name`.
2. Add a routing row to `skills/research-session/SKILL.md` only if the skill is user-facing.
   This changes the rules digest: recompute it and update
   `skills/research-session/references/review-rules-compatibility.json`, documenting why the
   digest changed and whether continuation is allowed.
3. Update the README capability matrix and the version in both manifests when releasing.
4. Add or update an offline test so the structural checks stay green.

## Changing a contract

- Additive optional fields keep the current major version.
- Removing fields, changing meaning, or changing enum semantics requires a new
  `*.v2.json`; keep v1 valid for at least one release cycle.
- Record the change (date, version, field, reason, migration) in `contracts/README.md`.

## Feedback

Use GitHub Issues once the repository is published; until then, use the issue templates in
`.github/ISSUE_TEMPLATE/` and keep reports local. Never paste credentials, private project
data, or ledger contents into an issue.
