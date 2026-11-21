## What changed

<!-- One logical change. Explain what and why. -->

## Evidence

<!-- Test command and result, or a minimal reproduction. Offline output preferred. -->

```text

```

## Checklist

- [ ] `python -m pytest tests skills/research-session/tests -p no:cacheprovider -q -ra` passes.
- [ ] Both plugin manifests still agree on version, license, repository, and metadata.
- [ ] README capability matrix and skill links still match the `skills/` tree.
- [ ] `CHANGELOG.md` updated for the affected version.
- [ ] No private paths, project names, credentials, ledger data, caches, or backup files added.
- [ ] Review gates, evidence binding, send lock, no-auto-resend, and the two-round ceiling are unchanged (or the change is explained).
- [ ] New behavior has an offline test; bug fixes include a test that fails before the fix.

## Notes for reviewers

<!-- Migration impact, skipped checks, or unverified environments. -->
