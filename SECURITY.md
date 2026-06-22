# Security Policy

## Supported versions

Local-81 is pre-1.0. Security fixes are applied to the latest released
minor series only; older snapshots are not maintained.

| Version | Supported |
|---------|-----------|
| 0.1.x   | yes       |
| < 0.1   | no        |

## Reporting a vulnerability

Please report security issues privately. **Do not open a public issue for a
suspected vulnerability.**

- Preferred: use GitHub's private vulnerability reporting on this repository
  (Security tab → "Report a vulnerability").
- Include: affected version/commit, a description of the impact, and the
  smallest set of steps or config needed to reproduce.

You can expect an initial acknowledgement within a few business days. Once a
fix is available it will be released and noted in `CHANGELOG.md`. Please give
us a reasonable window to remediate before any public disclosure.

## Scope notes

Local-81 is a push-based operator control plane that runs commands on remote
hosts over SSH. Reports that are especially in scope:

- Secret material written to plans, manifests, logs, or other on-disk artifacts.
- Command/argument injection in plan compilation or deploy execution.
- Access-policy or actor-check bypass in `deploy` / `compliance`.
- Privilege or path-traversal issues in rsync/ssh handling.

## Untrusted log data

Logs collected from remote hosts (`pull-logs`, `diag`) and remote command
output rendered by `logs` are treated as an **untrusted input channel** — they
may be read by operators in a terminal or by AI agents during triage, so a
payload embedded in a log must never be interpreted as an instruction
("Agentjacking"). Two independent controls in `local81.log_safety` run before
any log is consumed:

- **Merkle integrity.** Each capture writes `.local81-integrity.json`, a
  SHA-256-per-file manifest bound under a single RFC-6962 Merkle root (shared
  with the audit `ledger`). Consumption verifies content against it and reports
  any file altered, added, or removed after capture.
- **Injection scanning + sanitization.** Integrity proves bytes are
  *unchanged*, not *safe*. The scanner flags and the sanitizer neutralizes
  ANSI/terminal escape sequences, invisible/bidi Unicode and Unicode tag-char
  smuggling, and heuristic prompt-injection phrasing, leaving rendered text
  inert. This — not the Merkle check — is what stops an injected payload.
