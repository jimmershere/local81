# Changelog

All notable changes to Local-81 are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `LICENSE` (MIT) and `license` metadata in `pyproject.toml`.
- `SECURITY.md` security policy and this `CHANGELOG.md`.
- CI matrix across Python 3.12 and 3.13.
- `CLAUDE.md` cross-phase architecture notes and contributor guardrails.
- `local81.log_safety`: untrusted-log handling for log data pulled from remote
  hosts. `pull-logs` and `diag` now write a Merkle integrity manifest
  (`.local81-integrity.json`, reusing the `ledger` RFC-6962 primitives) over
  every collected file and scan it for injection payloads. Log rendering
  (`logs`) verifies content against the manifest and sanitizes terminal
  escapes, hidden/bidi Unicode, and prompt-injection phrasing before display,
  so log data is never consumed raw ("Agentjacking" hardening).
- Expanded the injection heuristics to cover common AI-agentic attack vectors:
  instruction override, jailbreak/persona, system/role-marker spoofing,
  tool-call / MCP manipulation, secret exfiltration, RCE / package-install
  (`curl | sh`, pip/npm/...), base64 & hex obfuscation, link injection, and
  social-engineering authorization claims. Findings now carry a severity
  (`warn` / `high`).
- `local81 scan` command: a standalone Merkle-integrity + injection-scan
  gateway over `log_safety` (write/verify manifest, sanitize copies, JSON
  report, `--fail-on` severity gate) — reusable from CI and integrations.
- `integrations/mcp/local81_log_guard.py`: a stdlib-only MCP server (Claude
  Desktop / Cursor) exposing `safe_read`, `scan_log`, `verify_integrity`, and
  `write_manifest`, so an agent reads logs through the guard instead of raw.
- `n8n/local81-log-guard.workflow.json`: a webhook gate that scans inbound
  untrusted log/error payloads and quarantines injection before forwarding.
- `.claude/skills/log-guard/SKILL.md`: a Claude Code skill for safely
  investigating logs/crash reports without acting on injected instructions.
- `site/portwright/ai-it-alerts.html`: editorial AI/IT security-alerts page
  artifact (Agentjacking explainer + remediation checklist) for portwright.io.
- `marketing/social-ad-local81-log-guard.md`: launch copy (drafts only;
  publishing/spend deferred to a human).

### Security
- Treat logs collected from remote hosts as an untrusted input channel.
  Integrity (Merkle) checks prove the bytes are unchanged since capture;
  sanitization/flagging is the control that actually neutralizes injected
  terminal-escape and prompt-injection payloads. Both run together before any
  consumption.

### Changed
- Corrected residual references to the project's former working name in docs and the logo seal to `LOCAL-81`.

### Removed
- Stray repo-root files (`floor2.txt`, `sf-integration-notes.md`, `.gitkeep`).
