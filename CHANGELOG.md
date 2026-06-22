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
