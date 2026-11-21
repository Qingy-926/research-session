# repro_governance plugin invocation (cross-client, via Git Bash)

## Location and invocation

- Set `PLUGIN_ROOT` to the independently installed public plugin root; do not assume a marketplace or shared-skill location.
- For a shared entry linked to a complete plugin installation, resolve the loaded `SKILL.md` path first; its skill directory is `<PLUGIN_ROOT>/skills/research-session`. Related skills are readable at `<PLUGIN_ROOT>/skills/<name>/SKILL.md` even when the host exposes only the shared entry. Do not copy a single skill directory and assume the plugin-level CLI or manifests were installed.
- CLI: `$PLUGIN_ROOT/scripts/repro_governance.py`, standard library only.
- stdout is UTF-8 JSON; use a UTF-8 terminal.

```bash
python "$PLUGIN_ROOT/scripts/repro_governance.py" <subcommand> --profile "<absolute profile.json path>"
```

## Eleven subcommands

| Command | Purpose | Typical time |
|---|---|---|
| status | Overview health check, preflight-style output | Optional fourth opening step |
| init | Initialize profile | First project integration |
| audit-paper | Paper audit | Before reproduction |
| freeze-protocol | Bind criteria to SHA-256 | Finalizing preregistration |
| preflight | Pre-execution checks | Before training/evaluation |
| verify-matrix | Matrix completeness | After grid results |
| audit-metrics | Metric audit | Before registration |
| verdict | Verdict semantics: BLOCKED / CRITERIA_MET / CRITERIA_NOT_MET | Closeout |
| snapshot-code | Code snapshot | Before execution |
| audit-logs | Log governance | Closeout |
| archive | Archive | End of project stage |

## Exit and verdict semantics

- `0` means pass; **CRITERIA_NOT_MET can also return exit 0**. Read JSON `failures` and `decision`, not just the exit code.
- `2` means missing evidence or failure. Report failures honestly; stop dependent steps, continue authorized work that does not depend on them, and do not relax gates.

## Independent review entry and read-only continuity check

This governance CLI is not the independent review dispatcher. Define `RS_SKILL_ROOT` as the absolute directory containing the research-session `SKILL.md` actually loaded in this host session, normally `<host-loaded plugin root>/skills/research-session`. Use the host-provided loaded path, not a maintenance source, another cache version, or a shared copy. If the loaded path is unknown, stop dependent dispatch rather than guessing.

Use the same root for `scripts/audit_dispatch.py`, `scripts/review_evidence.py` and `scripts/review_preflight.py`. Resolve paths from that root, not the project working directory. The read-only preflight never reads provider credentials/configuration, scans or writes the review ledger, probes the network, or sends a request:

```bash
RS_SKILL_ROOT="<absolute directory of the research-session SKILL.md loaded by this session>"
python -B "$RS_SKILL_ROOT/scripts/review_preflight.py" \
  --pack "<original bound pack>" --point 2 --inputs "<inputs.json>" --receipt "<old receipt>"
```

For a historical nondefault review model, give its original `--model` explicitly. Inspect the reported absolute dispatcher path, dispatcher SHA, skill version, actual rules SHA and exact compatibility pair. Exit 0 only means this local inspection is coherent; it never sets `gate_passed` or `review_completed`. Exact rule compatibility permits only the existing bounded continuation; it never inherits an old pass, resets attempts, creates a new review point, or authorizes sending. Changed results or dependencies stop registration/finalization and automatic reruns; authorized diagnosis, metric recomputation and clearly qualified drafts can continue. Ordinary `audit_dispatch.py --dry-run` is different: it may refresh ledger coverage and read managed provider state, so it is not this read-only check.

For a Responses receipt or a registered legacy route, the check retains only its structurally valid historical `route` binding and reports `route_validation=historical_binding_only`, `current_route_verified=false`. Legacy recognition is read-only and does not configure a current transport. Neither `receipt_current` nor `ready` verifies the current service route, reviewer identity or availability. The listed research actions remain blocked by this check alone even when local materials match; their existing authorization and review gates still apply. Use `-B` as shown to suppress Python bytecode-cache writes.

## Profile lookup fallback

1. Project root `governance-profile.json`.
2. `02-执行/governance/profile.json`.
3. Pointers in project `CLAUDE.md` or `README.md`.
4. If absent, report plugin not integrated and follow the normal process: skill §§1–6 do not depend on the plugin. You may propose `init`; do not create it unilaterally.

## Observed smoke test (2026-09-18, Git Bash)

`status` with a nonexistent profile returned **exit 2** and JSON:

```json
{
  "action": "preflight",
  "passed": false,
  "checks": [],
  "failures": [
    {"check": "profile", "context": "",
     "error": "[Errno 2] No such file or directory: '<传入路径>'"}
  ],
  "limitations": ["Checks validate recorded evidence; human/AI semantic review and truthful upstream artifacts remain necessary."],
  "generated_at": "<UTC 时间戳，+00:00>"
}
```

There was no Chinese output or mojibake on this path; JSON used ASCII escapes and timestamp was UTC.

## Boundaries

- Do not copy the plugin script into a project; always call the absolute path above so upgrades remain in the plugin directory.
- Give `--output` a **new path**. The plugin refuses to overwrite existing files to protect evidence; do not bypass it.
- The plugin performs mechanical checks only. Its `limitations` states: “Checks validate recorded evidence; human/AI semantic review and truthful upstream artifacts remain necessary.” Semantic review and upstream artifact truth still require human/AI judgment.
