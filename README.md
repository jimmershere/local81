# Local-81
![Local 81 — Fully Baked Orchestration. Clem the operator mascot on a Traverse City beachhead. Powered by Portwright.io — Built in Port, Proven at Sea.](docs/assets/local81-emblem.png)

[![CI](https://github.com/jimmershere/local81/actions/workflows/ci.yml/badge.svg)](https://github.com/jimmershere/local81/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](pyproject.toml)

**Operators’ Local-81 — plan, deploy, audit, hold the line.**

Local-81 is a lean, operator-readable deployment and runbook control plane: push-based over plain **SSH + rsync** with **no agent or daemon on your hosts**. Onboard a fleet, plan, deploy, repair, and prove every run with a tamper-evident Merkle audit ledger.

## See it run — the BAR Exam

A live, run-it-on-your-own-hardware stress test: Local-81 manages **10 heterogeneous Linux endpoints** — onboard them, deploy a release to all ten, break two on purpose, converge them back, and prove every run with the audit ledger.

**▶ Live demo & captured run — [portwright.io/local81](https://portwright.io/local81)**

Every panel on that page is real captured output (5–10s clips): deploy to ten hosts over SSH+rsync, drift detection & repair, and `audit verify` / `prove` / `emit`.

## What's inside

- **Fleet onboarding over SSH** — `keys`, `onboard`, `doctor --fleet`: keygen, `ssh-copy-id`, a read-only uptime/tooling sweep, and a per-fleet readiness report. Nothing lands on the targets.
- **Desired-state deploy & repair** — `plan`, `deploy`, upgrade, `rollback`; `deploy --check` detects drift and a converge run repairs only what moved, content-addressed by sha256.
- **Tamper-evident Merkle audit ledger** — every run is hash-chained and Merkle-rooted. `audit verify` recomputes the chain, `audit prove` gives an O(log n) inclusion proof, `audit emit` POSTs a signed receipt to a collector.
- **Secrets never at rest** — config holds *references* (`env://`, `bao://`, `sops://`, `delinea://`), never literals; sudo passwords stream in on stdin and are scrubbed from every artifact.
- **Web UI & automation** — render a multi-user [Semaphore](https://semaphoreui.com) front end on PostgreSQL 17, or drive the whole pipeline from an n8n workflow. The CLI stays the engine.
- **Compliance & multi-DB** — read-only NIST/CMS-mapped checks with advisory hardening plans, plus safe `db doctor`/`backup`/`audit` for PostgreSQL 17, Oracle 19c, and SQLite.

Support the line: Local-81 tee and mug runs may be offered for the Union Locals morale and welfare fund.

Feedback, issues, and borrowed workflow ideas are welcome — especially if they help make Local-81 simpler, tougher, and easier for another operator to trust.

## The name: a tribute to Local 81

This software is named **Local 81** in honor of the real **Local 81** — the bakery union local at the **Chef Pierre** plant in **Traverse City, Michigan** — the storied pie bakery (founded in 1922 and famous in the Cherry Capital of the World) where the members of Local 81 ran the line for many years.

We borrowed that union-hall ethos on purpose. A deployment tool, like a bakery floor, runs on the same values the Local stood for: skilled hands, dependable shifts, plans you can read, and work you can hand to the next operator with confidence. Every run is **union made** — planned, audited, and accountable. Calling it Local 81 keeps that heritage of organized, careful, take-pride-in-the-work labor at the center of the tool.

## Requirements

- Python 3.12 or newer
- bash
- ssh
- rsync
- find
- sha256sum

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .[dev]
local81 --help
```

## Common commands

```bash
local81 init --guided
local81 doctor
local81 compliance report
local81 compliance inventory --scope all
local81 compliance harden-plan --scope linux --format markdown
local81 db doctor
local81 db inventory
local81 plan --summary
local81 deploy --plan .local81/plans/<plan>.plan.json --check --dry-run
local81 history --limit 5
local81 logs <run-id>
local81 pull --scope main --dry-run
local81 diag --hosts app01 --dry-run
local81 status
```

Guided setup writes both `.local81/config.ini` for the current runtime and `.local81/config.yaml` as a human-readable mirror.

## Database operations

Local-81 includes a database operations command group for configured Oracle 19c, PostgreSQL 17, and SQLite targets:

```bash
local81 db doctor
local81 db tools --engine postgres17
local81 db diag --target appdb
local81 db backup --target local-sqlite --backup-path .local81/db/app.db.bak --execute
local81 db audit --format json
```

Database targets are configured as `[database "NAME"]` sections in `.local81/config.ini` or under `databases:` in YAML. Oracle and PostgreSQL integrations are safe external-tool plans and discovery checks; SQLite diagnostics and backups use Python stdlib `sqlite3`. Mutating actions are dry-run/planned unless `--execute` is supplied, and secrets must be passed as environment variable names or external references.

## Compliance and hardening

Local-81 includes read-only operational checks mapped to selected NIST/CMS control themes for Local-81 access policy, Linux OS settings, web server configuration, Java/Tomcat, JavaScript, Node.js, and Angular projects:

```bash
local81 compliance report --scope all
local81 compliance inventory --path /srv/myapp --format json
local81 compliance harden-plan --scope web --path /srv/myapp --format markdown
```

The compliance scanners inspect local files and emit findings, inventory, and hardening recommendations. They do not certify compliance, run package installs, run online audits, call `sudo`, call `sysctl -w`, restart services, change permissions, or edit configs. `harden-plan` is advisory only; it writes suggested remediation steps but does not apply them.

## Verify a fresh clone

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .[dev]
make test
make python-test
make quality
```

`make test` runs Python compile checks and the deterministic baseline shell regression suite. `make python-test` runs the Python pytest suite when development dependencies are installed. `make quality` runs compile, lint, format, pytest, and repository security gates. `make full-shell-test` runs every shell test, including advanced behavior tests that may be refined in later hardening phases.

## Repo layout

- `src/local81`, Python implementation
- `bin/local81`, source-tree launcher
- `docs/`, operator docs
- `packaging/`, Debian and RPM package scaffolds

## Operator docs

Start here if another tech needs to pick it up quickly:

- `docs/quickstart.md`
- `docs/setup-guide.md`
- `docs/commands.md`
- `docs/troubleshooting.md`
- `docs/guided-setup.md`
- `docs/when-to-use-local81.md`
- `examples/legacy-settings.cfg.example`
- `examples/profile-prod.yaml`
- `CONTRIBUTING.md`

## Install package builds

Debian/Ubuntu and RHEL-style package scaffolds live under `packaging/`.

See:
- `packaging/deb/README.md`
- `packaging/deb/build-deb.sh`
- `packaging/rpm/README.md`
- `packaging/rpm/local81.spec`
- `packaging/rpm/build-rpm.sh`

Both package formats install Local-81 as an application bundle under `/opt/local81`,
expose `/usr/bin/local81`, ship `/etc/local81/local81.ini.example`, and require a
Python 3.12 runtime on the target system.
