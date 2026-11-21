# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 5.x | Yes |
| < 5.0 | No (private snapshots, not distributed) |

## Reporting a vulnerability

- Do not open a public issue containing credentials, tokens, private research data, ledger
  contents, or machine paths.
- Once GitHub Security Advisories are available for this repository, report privately there.
  Until the repository is published, report to the maintainer listed in the plugin manifest
  (`@Qingy`) through a private channel, and keep the details local.
- Include: affected version, command or skill, expected vs. observed behavior, and a minimal
  reproduction that contains no real secrets.

## Credential handling

- Credentials are read only from the environment variable named by `api_key_env` (or
  `RESEARCH_SESSION_API_KEY_ENV`). They are never written to provider config files,
  receipts, review packs, run manifests, or logs.
- Provider configuration accepts only `url`, `model`, `provider`, and `api_key_env`.
- Do not commit a filled-in provider configuration. Use the example file and your own
  environment.
- A leaked key must be rotated at the provider; removing it from Git history is not enough.

## Sensitive local data

- The cross-project review ledger and send-lock records live under
  `~/.agents/state/research-review` (Windows: `%USERPROFILE%\.agents\state\research-review`).
  They contain project identities, `review_id` values, receipt paths, and hashes — not
  credentials. Treat them as private operational data.
- Run manifests written through `RESEARCH_SESSION_RUN_MANIFEST_DIR` contain versions, hashes,
  status, timings, and redacted errors. They are off by default and are local output.
- Do not attach ledgers, receipts, or run manifests to public reports without review.

## No telemetry

research-session contains no telemetry, analytics, or background network calls. Network
access happens only when an online review is explicitly configured and invoked, and only to
the endpoint you configured.
