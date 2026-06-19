# Web UI (Semaphore) and n8n orchestration

Local-81 stays a CLI with human-greppable on-disk artifacts. When you want a
multi-user web front end or an automated pipeline, you put one *in front of* the
CLI rather than turning Local-81 into an agent platform.

## Semaphore UI

[Semaphore](https://semaphoreui.com) (MIT) is the supported batteries-included
web UI: built-in users, LDAP/OIDC, and project-scoped RBAC. Render its config:

```bash
local81 ui semaphore-render \
  --db-host pg17.internal \
  --db-password-ref env://SEMAPHORE_DB_PASSWORD \
  --key-custody local81
```

This writes `.local81/ui/semaphore/{semaphore-config.json, local81-templates.json, render.json}`.

### Backing store — PostgreSQL 17

Semaphore is standardized on **PostgreSQL 17** (its recommended production
backend), with `sslmode=verify-full` for the DB link (SC-8). The DB password and
access-key encryption are emitted as `${SEMAPHORE_DB_PASSWORD}` /
`${SEMAPHORE_ACCESS_KEY_ENCRYPTION}` placeholders — **never literals**; you feed
them from your secret reference at runtime. Local-81's own `db doctor` /
`db backup` can manage that Postgres.

### Key-custody modes — who holds target SSH credentials

| Mode | `--key-custody` | Target creds | Compliance delta |
|---|---|---|---|
| **Thin (default)** | `local81` | Held by local81 (its keys / secret backends); Semaphore holds none. Templates shell out to the `local81` CLI. | None beyond "a web app with a login store". |
| **Shared Vault** | `vault` | Semaphore reads target creds from the same Vault/OpenBao local81 uses (`bao://`). | None durable in Semaphore. |
| **Native Key Store** | `semaphore` | Semaphore's Key Store holds target SSH keys / sudo passwords, AES-encrypted at rest. | **Introduces target creds at rest.** Refused unless `--accept-key-custody`; raises **L26-UI-001** (IA-5/SC-28). |

The default (`local81`) does not move the secrets-never-at-rest posture. Mode 1
is allowed but gated and flagged, so the choice is the operator's — made with
informed, recorded consent.

## n8n orchestration

`n8n/local81-deploy.workflow.json` is an importable workflow modeling the
agentic pipeline. Each step is an idempotent `local81` CLI call, so it maps 1:1
onto n8n nodes:

| Step | local81 | n8n node |
|---|---|---|
| Trigger | — | Webhook |
| Plan | `plan --ci` | Execute Command |
| Readiness gate | `doctor --fleet` | Execute Command + IF |
| Deploy | `deploy --latest --execute` | Execute Command |
| Emit receipt | (run summary) | HTTP Request |

Import via n8n → *Import from File*. Set `LOCAL81_DIR` and a `COLLECTOR_URL` for
the audit-receipt POST. Because every node is just the CLI, the same pipeline
runs by hand, from `cron`, from Semaphore, or from n8n unchanged.
