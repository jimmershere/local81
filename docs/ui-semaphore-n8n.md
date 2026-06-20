# Web UI (Semaphore) and n8n orchestration

Local-81 stays a CLI with human-greppable on-disk artifacts. When you want a
multi-user web front end or an automated pipeline, you put one *in front of* the
CLI rather than turning Local-81 into an agent platform.

## One catalog, every surface

A **recipe catalog** (`examples/recipes/fleet10.yaml`, schema
`local81.recipes.v1`) describes a fleet once — its roles, the action categories
an operator can run, the menu options inside each, the parameters those options
take, and the host groups — and every surface is *rendered* from it:

```bash
local81 ui semaphore-render --catalog examples/recipes/fleet10.yaml \
  --db-host pg17.internal --db-password-ref env://SEMAPHORE_DB_PASSWORD
local81 ui n8n-render   --catalog examples/recipes/fleet10.yaml
local81 ui stack-render --catalog examples/recipes/fleet10.yaml \
  --db-password-ref env://SEMAPHORE_DB_PASSWORD
```

Edit one host, port, action, or menu option in the catalog and re-render; the
web UI, the n8n workflows, and the docker fleet all change together. The catalog
is strictly validated (unknown keys, dangling group members, and command
placeholders with no declared parameter are errors, not silent defaults), the
same fail-closed posture `doctor` takes with `config.ini`.

The behaviour behind every button is one generated **dispatcher script** per
category (`dispatch/<category>.sh`). Both the Semaphore template and the n8n
workflow shell out to it, so a button-press, a webhook, and an operator at a
shell all run the identical `local81` command. The dispatcher is plain bash with
defaulted variables — readable, greppable, runnable by hand.

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

### Survey-driven templates (the easy button)

With `--catalog`, the five placeholder templates become one **survey-driven
template per action category**. Running one prompts the operator with dropdowns
instead of a command line:

* **Action** — the options in that category (Deploy → plan / dry-run / deploy /
  drift-check / rollback).
* **Which hosts** — a host group (All / Web tier / API tier / Data tier /
  Workers), shown for deploy-family actions.
* **Parameters** — one field per `{placeholder}` the category uses (`forks`,
  `scope`, `keep`, …); `enum` parameters are themselves dropdowns.

The template runs `dispatch/<category>.sh`, which maps the choices to the right
`local81` invocation. Mutating actions (deploy, rollback) are labelled as such.
Plus one **build template per recipe** to stand a role's image/workload up. The
render writes `templates.json` (import these), `dispatch/*.sh`, and the usual
`semaphore-config.json` / `render.json`.

## Runnable stack — `ui stack-render`

For a turnkey demo, `stack-render` emits a directory you can bring up directly:

```bash
local81 ui stack-render --catalog examples/recipes/fleet10.yaml \
  --db-password-ref env://SEMAPHORE_DB_PASSWORD
cd .local81/ui/stack
cp .env.example .env          # fill blanks from your secret references
docker compose -f docker-compose.semaphore.yml --env-file .env up -d
open launcher.html            # the friendly landing page
```

It contains the Semaphore + **PostgreSQL 17** compose, a `.env.example` whose
credentials are all placeholders (never literals), a static **launcher page**
rendered from the catalog (categories as cards, host groups as chips, a
4-step "pick → fill → Run" guide), an `import.sh` helper, and
`fleet/docker-compose.fleet.yml` — the catalog's roles as buildable containers,
so there's a real fleet to deploy to.

## n8n orchestration

`n8n/local81-deploy.workflow.json` is the hand-built reference pipeline (plan →
gate → deploy → emit). For breadth, `ui n8n-render --catalog ...` generates **one
workflow per category**: a webhook whose Execute Command node shells the same
`dispatch/<category>.sh` the Semaphore template uses.

| Step | local81 | n8n node |
|---|---|---|
| Trigger | — | Webhook (`POST /local81-<catalog>-<category>`) |
| Dispatch | `dispatch/<category>.sh` (→ `local81 …`) | Execute Command |

Import via n8n → *Import from File*. Set `LOCAL81_DIR`; the webhook body's
`action`/`hosts`/parameter fields drive the dispatcher. Because every node is
just the CLI behind a shared script, the same operation runs by hand, from
`cron`, from Semaphore, or from n8n unchanged.

## Catalog schema (`local81.recipes.v1`)

| Block | Purpose | Renders into |
|---|---|---|
| `categories[].options[]` | The action menus (`command` is the CLI tail; `mutating` flags state changes) | Semaphore action dropdown · n8n routes · dispatcher case arms |
| `parameters[]` | Named `{placeholder}` knobs (`int`/`str`/`enum`) | Survey fields · n8n body inputs |
| `groups[]` | Named host subsets (members are recipe keys) | "Which hosts" dropdown · dispatcher `members()` |
| `recipes[]` | The roles: `build` (base/packages/workload), `deploy.target_dir`, `health`, `test` | Fleet compose · `Dockerfile.role` · build templates · verification matrix |
