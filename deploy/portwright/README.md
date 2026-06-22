# Publishing portwright.io (the editorial page)

The connector is `.github/workflows/deploy-portwright.yml`. It publishes
`site/portwright/` to the portwright.io droplet **on a successful merge to
`main`** (and via manual `workflow_dispatch`). Credentials never live in the
repo — the workflow reads repository **secrets**, and a `production`
environment provides the post-review approval gate.

## One-time setup

### 1. Review gate (so a merge waits for a human "publish" approval)

In **Settings → Environments**, create an environment named **`production`**
and add yourself under **Required reviewers**. The deploy job targets that
environment, so each run pauses until you approve — this is the "publish once
reviewed" gate.

### 2. Deploy secrets (Settings → Environments → production → Secrets)

| Secret | Required | Meaning |
|---|---|---|
| `PORTWRIGHT_HOST` | yes | Droplet hostname or IP |
| `PORTWRIGHT_SSH_KEY` | yes | **Private** deploy key (PEM). Use a dedicated deploy key, not a personal `id_rsa`. |
| `PORTWRIGHT_USER` | no | SSH user (default `root`) |
| `PORTWRIGHT_PATH` | no | Web root (default `/var/www/portwright.io`) |
| `PORTWRIGHT_KNOWN_HOSTS` | recommended | Pinned `known_hosts` line for the droplet; if unset the workflow falls back to `ssh-keyscan` (TOFU). |

Until `PORTWRIGHT_HOST` and `PORTWRIGHT_SSH_KEY` are set, the job **skips
gracefully** (no failed builds) and prints a warning.

### Generating a dedicated deploy key (recommended over a personal id_rsa)

```bash
ssh-keygen -t ed25519 -f portwright_deploy -C "portwright.io deploy" -N ""
# Put the PUBLIC key on the droplet:
ssh-copy-id -i portwright_deploy.pub root@<droplet>     # or append to ~/.ssh/authorized_keys
# Paste the PRIVATE key (portwright_deploy) into the PORTWRIGHT_SSH_KEY secret.
# Pin the host key:
ssh-keyscan -H <droplet>     # paste output into PORTWRIGHT_KNOWN_HOSTS
```

## Security notes

- The page is published **without** `rsync --delete`, so it won't clobber the
  rest of the site; it lands at `https://portwright.io/ai-it-alerts.html`.
- Prefer a **dedicated, least-privilege deploy key** over reusing a personal
  `~/.ssh/id_rsa`. Rotate it if it is ever exposed.
- Nothing here uses or stores the GitHub PAT — that token is only for pulling
  the private `clemtock` assets locally and never belongs in CI or the repo.
