# Local-81 RECIPE — onboard endpoints (EPs) from a CSV

**Short answer to "is this already a thing?":** yes — Local-81 already onboards the
*right* way with `keys`, `onboard`, and `doctor --fleet` (key-based SSH, no agent
on the target, **dry-run first**, `--execute` to act). What it didn't have is a way
to drive that from a plain `hostname ip group purpose` file. This recipe adds that
thin front-end: `onboard_from_csv.py` turns your CSV into the inputs those commands
already consume. You are not bypassing anything — you're feeding the real tools.

## 1. Write your endpoints file (space-separated)

```
# hostname   ip            group       purpose/tag
web01        10.0.0.11     web-tier    nginx-frontend
papi01       10.0.0.21     api-tier    python-api
pgdb01       10.0.0.31     data-tier   postgres-17
```
Blank lines and `#` comments are fine. Duplicate hostnames/IPs and short rows are
**hard errors** (fail-closed), so a typo can't silently onboard the wrong box.

## 2. Generate the Local-81 inputs (no host is contacted)

```bash
python3 onboard_from_csv.py endpoints.csv --out onboarding-out --user operator
```
Writes into `onboarding-out/`:
| file | what it's for |
|---|---|
| `ssh_config` | one `Host` block per EP (name → ip) — append to `~/.ssh/config` so Local-81 can address EPs by friendly name |
| `hosts.txt`  | one hostname per line — for `--hosts-file` |
| `groups.txt` | group → members map — for targeting a tier with `--hosts a,b,c` |
| `fleet.yaml` | a validated `local81.recipes.v1` catalog (groups + one recipe per EP, `role=purpose`) — drives `ui semaphore-render` / `ui n8n-render` / deploy |

Flags: `--user` (EP login user), `--identity` (control key, default `~/.ssh/id_ed25519`),
`--base-port` (placeholder ports for the catalog), `--fleet-name`.

## 3. Wire up SSH + the control key

```bash
cat onboarding-out/ssh_config >> ~/.ssh/config   # review first; merge, don't blind-append twice
local81 keys ensure --execute                    # ed25519 control keypair (idempotent)
local81 keys check                               # confirm key + perms + agent
```

## 4. Onboard — dry-run, then for real

```bash
# Plan + READ-ONLY sweep (uptime + sshd/python3/rsync/find/sha256sum), changes nothing:
local81 onboard --hosts-file onboarding-out/hosts.txt

# Go live: generate/copy the key (ssh-copy-id) and re-sweep to a readiness report:
local81 onboard --hosts-file onboarding-out/hosts.txt --execute
```
First contact uses `StrictHostKeyChecking accept-new` (won't block); **pin host keys**
after the first clean onboarding for steady-state security.

> Per-tier instead of all-at-once? Use the `groups.txt` map:
> `local81 onboard --hosts $(grep '^web-tier:' onboarding-out/groups.txt | cut -d' ' -f2) --execute`

## 5. Prove the fleet is ready

```bash
local81 doctor --fleet     # every EP: reachable + carries the 5 required tools, nothing else
```

## 6. (Optional) drive everything from the generated catalog

`fleet.yaml` is the single source of truth for the rest of Local-81 — same fleet,
every surface re-renders from it:

```bash
local81 ui semaphore-render --catalog onboarding-out/fleet.yaml --db-host <pg-host>
local81 ui n8n-render       --catalog onboarding-out/fleet.yaml
local81 plan --summary       # once a .local81/config.ini references these servers/groups
```
Edit the placeholder `port:` values in `fleet.yaml` to each EP's real service port
before using the deploy/build matrix.

## Why this is the right way
- **No agent** ever lands on the EP — just your SSH key + the five POSIX tools.
- **Dry-run first**: the read-only sweep tells you an EP is onboardable before a byte changes.
- **One readable file** in, version-controllable; one validated catalog out that the whole control plane reads.
- **Fail-closed** parsing: typos and duplicates are errors, not silent surprises.
