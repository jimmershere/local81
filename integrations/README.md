# Integrations — Local-81 Log Guard

Ways to put Local-81's untrusted-log defense (Merkle integrity + injection
scanning/sanitization) in front of the systems that consume logs. All of them
wrap the same engine (`local81.log_safety`, exposed by `local81 scan`), so the
behavior is identical everywhere.

| Path | What it is | Use it for |
|---|---|---|
| `mcp/` | Stdlib MCP server (`safe_read`, `scan_log`, `verify_integrity`, `write_manifest`) | Claude Desktop / Cursor: let the agent read logs through the guard instead of raw |
| `claude-skill/` | A Claude Code skill | Teach Claude Code to investigate logs without acting on injected instructions |
| `../n8n/local81-log-guard.workflow.json` | n8n webhook gate | Scan/quarantine inbound error-tracker payloads before they reach a downstream agent |

Background and the threat model: `../SECURITY.md` → "Untrusted log data".

## The honest one-liner

A Merkle/integrity check proves log bytes are **unchanged since capture** — it
is tamper-evidence, not safety. A faithfully recorded malicious log hashes
fine. The **sanitization + injection scanning** is what actually neutralizes an
injected payload. Log Guard ships both, and never executes what it finds.
