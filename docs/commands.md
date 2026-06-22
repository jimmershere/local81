# LOCAL-81 Command Reference v0.1

## Overview

```
local81 init [--import PATH] [--force] [--project NAME] [--guided]
local81 doctor [--plan PATH]
local81 db <doctor|inventory|tools|monitor|diag|backup|report|audit> [options]
local81 compliance <report|inventory|harden-plan> [options]
local81 status
local81 hooks
local81 profiles
local81 profile create NAME
local81 plan [--scope NAME] [--format json] [--stdout] [--ci] [--summary]
local81 deploy (--plan PATH | --latest) [options]
local81 pull [--scope NAME] [--hosts host1,host2] [--rsync-opts OPTS] [--dry-run]
local81 history [--limit N]
local81 pull-logs [options]
local81 diag [options]
local81 logs RUN_ID
local81 diff PLAN_A PLAN_B
```

Global option:

```
local81 --profile PROFILE <command>
```
Use `--profile` to apply a profile overlay from `.local81/profiles/` when the command supports it.

---

## `local81 init`

Initializes the `.local81/` workspace directory structure and generates `config.ini` by importing a legacy `settings.cfg` or a modern config. Guided mode also writes a matching `config.yaml` mirror.

| Flag | Description |
|---|---|
| `--import PATH` | Path to legacy or modern config to import |
| `--force` | Overwrite existing `.local81/config.ini` |
| `--project NAME` | Override project name in generated config |
| `--guided` | Launch the interactive guided setup flow instead of importing config |

---

## `local81 plan`

Generates a deployment plan from `.local81/config.ini` and writes it to `.local81/plans/`.

| Flag | Description |
|---|---|
| `--scope NAME` | Restrict plan to a single scope |
| `--format json` | Output format (only `json` supported) |
| `--stdout` | Print plan JSON to stdout in addition to writing file |
| `--ci` | Print plan JSON to stdout only, skip writing file |
| `--summary` | Print a compact one-liner per step instead of full JSON |

### `--summary` output format

When `--summary` is specified, one line per step is printed to stdout:

```
step_id | type | timeout | status
```

- `step_id` — stable step identifier (e.g. `scope:myapp:0001`)
- `type` — step type: `mkdir`, `rsync`, or `remote_cmd`
- `timeout` — per-step timeout in seconds, or `-` if not set
- `status` — always `pending` for a newly generated plan

The plan file is still written to `.local81/plans/` unless `--ci` is also set.

Example output:
```
scope:web:0001 | mkdir | - | pending
scope:web:0002 | rsync | - | pending
scope:web:0003 | rsync | 30 | pending
```

---

## `local81 deploy`

Executes a deployment plan.

Before any steps run, deploy enforces the configured `[access]` policy from `.local81/config.ini`. A deploy is blocked when the current operator is denied by user/group policy or when selected plan steps include `remote_cmd` while `access.allow_remote_cmd = false`.

| Flag | Description |
|---|---|
| `--plan PATH` | Path to a specific `.plan.json` file |
| `--latest` | Use the most recently generated plan file |
| `--dry-run` | Print steps without executing |
| `--scope NAME` | Execute only steps for the named scope |
| `--max-parallel N` | Max concurrent workers (CLI default: 1) |
| `--step-timeout SECS` | Default timeout per step in seconds |
| `--rollback-on-failure` | On failure, execute rollback cmds for successful prior steps |
| `--fail-fast` / `--no-fail-fast` | Override `fail_fast` behavior |
| `--check` | Validate the plan, config schema, access policy, execution inputs, and (for v2 plans) re-gather facts to report target drift before running steps |
| `--allow-drift` | With `--check`: report a target diverging from the plan's desired state as a warning instead of failing |
| `--hosts-file PATH` | Restrict execution to hosts listed in a file |
| `--parallel` | Enable parallel host execution where supported |
| `--forks N` | Fleet mode: max hosts executing concurrently within a batch (default 5) |
| `--serial N\|N%` | Fleet mode: rolling batch size; batches run sequentially (default: one batch) |
| `--max-fail N\|N%` | Fleet mode: abort new batches once failures reach the threshold (default 0 = first failure) |
| `--limit GLOB` | Fleet mode: restrict to plan hosts matching a glob pattern |
| `--notify` | Emit notification output for wrapped/operator workflows |
| `--quiet` | Reduce normal progress chatter |

Any of `--forks`, `--serial`, `--max-fail`, or `--limit` activates **fleet
mode**: the plan's distinct step hosts become the fleet, are partitioned into
rolling `--serial` batches, and each batch runs up to `--forks` hosts at once.
After every batch the cumulative failure count is checked against `--max-fail`;
once breached, remaining hosts are marked `skipped` and no new batch starts. A
final summary reports `ok / changed / unchanged / failed / skipped`, and each
host's steps are written to `.local81/runs/<run-id>/<host>.log` (view with
`local81 logs <run-id> --host <host>`). See [Fleet execution](fleet.md).

### Recommended first live deploy

```bash
local81 deploy --plan .local81/plans/<plan-id>.plan.json --scope main --dry-run --fail-fast
local81 deploy --plan .local81/plans/<plan-id>.plan.json --scope main --fail-fast
```


---

## `local81 db`

Manages database operational checks and reports for configured Oracle 19c, PostgreSQL 17, and SQLite targets.

Subcommands:

- `doctor` — validate target config, discover tools, and run safe readiness checks
- `inventory` — list configured targets and tool availability
- `tools` — show database tool discovery status
- `monitor` — emit monitoring readiness plans
- `diag` — run or plan diagnostics
- `backup` — plan backups, or execute supported SQLite backups with `--execute`
- `report` — write normalized JSON and text reports
- `audit` — emit audit/compliance readiness checks

Common options:

| Flag | Description |
|---|---|
| `--config PATH` | Config file to read, default `.local81/config.ini` with YAML fallback |
| `--target NAME` | Restrict to one database target |
| `--engine oracle19c|postgres17|sqlite` | Restrict by engine |
| `--output-dir DIR` | Artifact root, default `.local81/db` |
| `--format text|json` | Terminal output format |
| `--quick` | Skip slower checks where supported |
| `--execute` | Required for state-changing actions |
| `--backup-path PATH` | Destination for executable SQLite backups |

Reports are written under `.local81/db/<run-id>/summary.json` and `report.txt`.

Oracle and PostgreSQL support is intentionally CLI-oriented. Local-81 discovers tools and emits redacted command plans for SQL*Plus/SQLcl, RMAN, Data Pump, AHF/ORAchk, `psql`, native PostgreSQL backup tools, Barman, pgBackRest, exporters, and pgBadger without requiring live services in CI. SQLite uses Python stdlib `sqlite3` for read-only PRAGMA diagnostics and optional online backup.

Examples:

```bash
local81 db doctor
local81 db tools --engine oracle19c
local81 db diag --target app-sqlite --quick
local81 db backup --target app-sqlite --backup-path .local81/db/app.sqlite.bak --execute
```

---

## `local81 status`

Shows the current deployment run state:

- Any active runs (in-progress, based on presence of run.json in run dirs)
- Last run result (pass/fail)
- Last run timestamp
- Last run ID

**State sources (in order of priority):**

1. `~/.local81/state.json` — global state file (written by external integrations)
2. `/tmp/local81-state.json` — fallback temp state file
3. `.local81/runs/` — scans the latest `run.json` in the local project

**State file format** (`~/.local81/state.json`):
```json
{
  "last_run_id": "20260101T000000Z-12345",
  "last_result": "pass",
  "last_run_at": "2026-01-01T00:01:00Z",
  "last_rc": 0
}
```

Example output:
```
Local-81 status
============

Active runs:
  None right now.

Latest run:
  Run ID: 20260101T000000Z-12345
  Result: pass
  Exit code: 0
  Finished: 2026-01-01T00:01:00Z
```

---

## `local81 pull`

Pulls files back from remote hosts using the current project config.

| Flag | Description |
|---|---|
| `--scope NAME` | Restrict pull to a named scope |
| `--hosts host1,host2` | Restrict to specific hosts |
| `--rsync-opts OPTS` | Extra rsync options to append |
| `--dry-run` | Print the pull actions without executing |

Use this when you need reverse sync from a remote system back into the local workspace.

---

## `local81 history`

Shows recent deployment runs, newest first.

| Flag | Description |
|---|---|
| `--limit N` | Maximum number of runs to show (default: 20) |

---

## `local81 pull-logs`

Collects remote application logs into a local destination folder.

| Flag | Description |
|---|---|
| `--settings PATH` | Legacy settings file to read (default: `./settings.cfg`) |
| `--hosts host1,host2` | Restrict to specific hosts |
| `--dest PATH` | Local destination directory |
| `--jboss-path PATH` | Override JBoss log path |
| `--apache-path PATH` | Override Apache log path |
| `--engin-path PATH` | Override Engin log path |
| `--smartxfr-path PATH` | Override SmartXfr log path |

After collection, `pull-logs` writes a Merkle integrity manifest
(`.local81-integrity.json`) over the pulled tree and scans every file for
injection content. Logs from remote hosts are untrusted input: integrity
proves the bytes are unchanged since capture, while the scan/sanitization is
what neutralizes terminal-escape and prompt-injection payloads. See
`SECURITY.md` → "Untrusted log data".

---

## `local81 diag`

Runs remote diagnostics against one or more hosts.

| Flag | Description |
|---|---|
| `--project NAME` | Project label for the diagnostic bundle |
| `--hosts host1,host2` | Restrict to specific hosts |
| `--diag-type TYPE` | Diagnostic type, for example `strace` |
| `--pid PID` | Process ID for targeted diagnostics |
| `--duration VALUE` | Capture duration, for example `20s` |
| `--remote-cmd CMD` | Custom remote command override |
| `--out-dir PATH` | Local output directory |
| `--ssh-user USER` | Override SSH user |
| `--include-disabled` | Include hosts marked disabled in config |
| `--dry-run` | Print the diagnostic actions without executing |

---

## `local81 logs`

Shows detailed step-by-step output for a single run. `RUN_ID` may be a full run ID or an unambiguous prefix.

Remote command output and per-host logs are sanitized before display (ANSI
escapes, hidden/bidi Unicode, prompt-injection phrasing are flagged and
neutralized) and, when a run directory carries an integrity manifest, verified
against it. A warning banner is printed when content is flagged or fails
integrity.

---

## `local81 scan`

Merkle integrity + injection scan for untrusted log data. A standalone gateway
over the same `log_safety` engine that guards `pull-logs` / `diag` / `logs`, so
it is reusable from CI, an n8n node, a Claude Code skill, or the bundled MCP
server. It never executes anything it finds.

| Flag | Description |
|---|---|
| `paths...` | Files or directories to scan |
| `--manifest-dir DIR` | Directory whose integrity manifest to write/verify |
| `--write-manifest` | Write a Merkle integrity manifest over the directory |
| `--verify` | Verify content against an existing integrity manifest |
| `--sanitize-to PATH` | Write sanitized (inert) copies of scanned files |
| `--json` | Emit a machine-readable JSON report |
| `--fail-on {never,warn,high}` | Minimum severity that exits non-zero (default `high`) |
| `--quiet` | Suppress human-readable output |

Exit codes: `0` clean, `3` injection findings at/above the threshold, `4`
integrity failure. Example as a CI gate over collected logs:

```bash
local81 scan .local81/pulled-logs --verify --fail-on high
```

See `integrations/` for the n8n Log Guard workflow, the Claude Desktop MCP
server, and the `log-guard` Claude Code skill that wrap this engine, and
`SECURITY.md` → "Untrusted log data".

---

## `local81 diff`

Compares two generated plan files.

Usage:

```bash
local81 diff .local81/plans/old.plan.json .local81/plans/new.plan.json
```

Use this when you want a quick before/after check on plan generation changes.

---

## `local81 schedule`

Renders operator-installable **systemd** `.service` + `.timer` units for
recurring local81 commands. Local-81 never installs or runs a scheduler itself;
it emits plain files into `.local81/schedules/` that you copy into
`/etc/systemd/system`. See [Scheduling & triggers](scheduling.md).

Subcommands:

```bash
local81 schedule add NAME --command "<argv>" --on-calendar "<spec>" \
    [--notify-url URL] [--description TEXT] [--working-dir DIR]
local81 schedule list
local81 schedule remove NAME
local81 schedule doctor
```

The rendered wrapper runs the command under `flock -n`, so a slow run never
overlaps the next timer fire. An optional `--notify-url` becomes a best-effort
`ExecStartPost` webhook (e.g. to n8n).

---

## `local81 rollback`

Reverses a **completed** deploy run after the fact, using the per-step rollback
commands recorded in its `run.json`. Like every mutating action it is **dry-run
unless `--execute`**. See [Rollback](rollback.md).

```bash
local81 rollback RUN_ID            # show the reverse plan only
local81 rollback RUN_ID --execute  # apply it, writing a new rollback.json record
```

Only steps that recorded a concrete rollback command (today: a `cp -a` restore
from the rsync `--backup` copy) are reversed, in LIFO order. Steps that made no
change, did not succeed, were skipped, or have no recorded reverse command are
surfaced as explicit **skips with a reason** — never silently treated as undone.

---

## `local81 gc`

Prunes old run directories under `.local81/runs/` by **count** and/or **age**.
Dry-run unless `--execute`.

```bash
local81 gc --keep 20                 # preview: keep newest 20, drop the rest
local81 gc --max-age-days 90         # preview: drop anything older than 90 days
local81 gc --keep 20 --max-age-days 90 --execute
```

With both bounds set, a run survives only if it is within the newest `--keep`
*and* younger than `--max-age-days`. With neither, nothing is pruned.

---

## `local81 hooks`

Lists supported hook paths and whether each hook is installed and executable.

---

## `local81 profiles`

Lists available profile overlays from `.local81/profiles/`.

---

## `local81 profile create`

Creates a scaffold profile file at `.local81/profiles/<name>.yaml`.

---

## `local81 doctor`

Checks environment health and optionally validates a plan file schema.

| Flag | Description |
|---|---|
| `--plan PATH` | Path to a `.plan.json` file to validate |

**Checks performed:**

| Check | Level | Description |
|---|---|---|
| `binary:bash` | PASS/FAIL | bash in PATH |
| `binary:python3` | PASS/FAIL | python3 in PATH |
| `binary:ssh` | PASS/FAIL | ssh in PATH |
| `binary:rsync` | PASS/FAIL | rsync in PATH |
| `binary:find` | PASS/FAIL | find in PATH |
| `binary:sha256sum` | PASS/FAIL | sha256sum in PATH |
| `binary:git` | PASS/WARN | git in PATH (warn if missing; only required for `workspace=git`) |
| `dir:~/.local81` | PASS/WARN | global state dir readable+writable |
| `dir:.local81` | PASS/WARN | project workspace dir |
| `dir:.local81/plans` | PASS/WARN | plans dir |
| `dir:.local81/runs` | PASS/WARN | runs dir |
| `dir:.local81/state` | PASS/WARN | state dir |
| `plan:json` | PASS/FAIL | plan file parses as valid JSON |
| `plan:schema` | PASS/FAIL | required top-level keys present |
| `plan:kind` | PASS/FAIL | `kind == "plan"` |
| `plan:mode` | PASS/FAIL | `mode == "deploy"` |
| `plan:schema_ver` | PASS/FAIL | `schema` is `local81.plan.v2` or `local81.plan.v0.1` |
| `plan:scopes` | PASS/WARN | scopes list present and non-empty |
| `plan:steps` | PASS/WARN | total step count across all scopes |
| `config:schema` | PASS/WARN/FAIL | `.local81/config.ini` schema validation |

**Output format:**
```
[PASS] binary:bash: /bin/bash
[PASS] binary:python3: /usr/bin/python3
[WARN] dir:.local81: does not exist
[FAIL] plan:kind: expected 'plan', got 'notaplan'
```

Exit code: `0` if all checks pass, `1` if any check is FAIL.

---

## `local81 compliance report`

Prints a read-only operational hardening report mapped to selected NIST/CMS control themes. The report includes the existing Local-81 access-control policy checks plus optional local scanners for Linux OS configuration, web server settings, Java/Tomcat, JavaScript, Node.js, and Angular projects.

This command reports evidence and recommendations; it does not certify a system or prove compliance.

The report includes:

| Finding | Control theme | Description |
|---|---|---|
| `[access]` loaded | AC-3 | Confirms whether an access policy is configured |
| Allowed users/groups | AC-2 | Warns when deploy access is unrestricted |
| Root execution policy | AC-6 | Warns unless `deny_root = true` |
| Remote command policy | CM-5 | Passes when `allow_remote_cmd = false` |
| Runtime/artifact permissions | AC-6 | Confirms directories/files are owner-only by design |
| Linux OS settings | CM-6, SC-7, IA-5, AU-12 | Checks readable SSH, sysctl, fstab, sudo/PAM, audit, and permission evidence |
| Web server settings | SC-8, SC-13, SC-7, AU-12 | Checks Apache/Nginx/Tomcat TLS, header, logging, timeout, token, and TRACE evidence |
| Java/Tomcat settings | AC-3, CM-7, SC-7 | Checks JDWP, JMX, Tomcat shutdown port, manager/default apps, and access logging evidence |
| JS/Node/Angular settings | CM-6, SA-10, SI-10, SC-28 | Checks lockfiles, package manager pinning, lifecycle scripts, npm config, source maps, and risky Angular patterns |

Options:

| Flag | Description |
|---|---|
| `--root PATH` / `--path PATH` | Root path to inspect, default current directory |
| `--scope all|access|linux|os|web|java|javascript|node|angular` | Restrict scanner scope |
| `--profile NAME` | Compliance profile label for the report |
| `--format text|json|markdown` | Output format |
| `--include-passed` / `--no-include-passed` | Include passing findings, or show only gaps |
| `--fail-on never|low|medium|high|critical` | Exit nonzero when a failed finding meets the threshold |
| `--output-dir DIR` | Write `.txt` and `.json` artifacts under a timestamped directory |

Exit code: `0` unless a failed finding meets `--fail-on`. Defaults to `--fail-on high`.

## `local81 compliance inventory`

Lists discovered compliance evidence candidates without judging them. This command is read-only and never fails due to hardening findings.

Examples:

```bash
local81 compliance inventory --path /srv/myapp
local81 compliance inventory --path /srv/myapp --format json
```

## `local81 compliance harden-plan`

Generates a non-mutating remediation plan from compliance findings. It prints recommended settings and manual review notes but does not edit files, change permissions, restart services, run `sudo`, install packages, or contact external services.

Examples:

```bash
local81 compliance harden-plan --scope linux --path /
local81 compliance harden-plan --scope web --path /srv/myapp --format markdown
```
