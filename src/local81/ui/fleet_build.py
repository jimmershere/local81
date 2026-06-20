"""Shared fleet-build rendering: the role image + per-recipe build scripts.

These are referenced by the Semaphore *build templates* (one per recipe) and by
the runnable stack, so the implementation lives here once. Each per-recipe
script is **self-contained and idempotent**: it builds the shared role image if
missing, ensures the network, and (re)launches just that role's container. That
is what makes a freshly rendered demo functional out of the box — click the
build template for a role, or run the script by hand, and the host comes up.

Pure: returns strings, writes nothing.
"""

from __future__ import annotations

import shlex

from ..recipes import Catalog, Recipe


def _shq(value: str) -> str:
    """Shell-quote a value for safe embedding in a generated script."""
    return shlex.quote(value)


def _shared_packages(catalog: Catalog) -> str:
    pkgs: list[str] = []
    for r in catalog.recipes:
        for p in r.packages:
            if p not in pkgs:
                pkgs.append(p)
    return " ".join(sorted(pkgs))


def role_dockerfile(catalog: Catalog) -> str:
    """The one image every role runs (role chosen at container run time)."""
    return f"""# Catalog-driven fleet image ({catalog.name}) — one image, role at run time.
# Satisfies local81's only endpoint needs: sshd + python3 + rsync + find + sha256sum.
FROM debian:12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \\
        {_shared_packages(catalog)} \\
    && rm -rf /var/lib/apt/lists/* \\
    && mkdir -p /var/run/sshd /srv/app /var/log/app \\
    && groupadd -f operator && useradd -m -s /bin/bash -g operator operator \\
    && mkdir -p /home/operator/.ssh && chmod 700 /home/operator/.ssh
ARG PUBKEY=""
RUN echo "$PUBKEY" > /home/operator/.ssh/authorized_keys \\
    && chmod 600 /home/operator/.ssh/authorized_keys \\
    && chown -R operator:operator /home/operator/.ssh /srv/app /var/log/app
# Generic entrypoint: record the role, run its (optional) workload, then sshd in
# the foreground so local81 reaches it like any host. ROLE + WORKLOAD are passed
# at run time from the catalog, so this one image serves every role honestly.
RUN printf '%s\\n' \\
      '#!/bin/bash' \\
      'echo "${{ROLE:-generic}}" > /etc/local81-role' \\
      'mkdir -p /srv/app /var/log/app' \\
      'chown -R operator:operator /srv/app /var/log/app 2>/dev/null || true' \\
      '[ -n "${{WORKLOAD:-}}" ] && ( eval "$WORKLOAD" >/var/log/app/workload.log 2>&1 & )' \\
      'exec /usr/sbin/sshd -D -e' > /usr/local/bin/entrypoint.sh \\
    && chmod +x /usr/local/bin/entrypoint.sh
EXPOSE 22
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
"""


def role_build_script(catalog: Catalog, recipe: Recipe) -> str:
    """A standalone build+launch script for one role — the build template body."""
    # Heredoc body for the shared image, built inline so the script needs no
    # other files on disk. Indented two spaces to sit inside the if-block.
    dockerfile = role_dockerfile(catalog)
    return f"""#!/usr/bin/env bash
# Build + launch the {recipe.role} workload ({recipe.title}) on '{recipe.alias}',
# from catalog '{catalog.name}'. Self-contained and idempotent: safe to re-run.
# Referenced by the Semaphore build template for this recipe.
set -euo pipefail
: "${{PUBKEY:?export PUBKEY=\\"$(cat ~/.ssh/your_key.pub)\\" (the demo public key)}}"
IMG="${{ROLE_IMAGE:-{catalog.name}-role:latest}}"
NET="${{FLEET_NET:-{catalog.name}net}}"
ALIAS="{recipe.alias}"; ROLE="{recipe.role}"; PORT="{recipe.port}"
WORKLOAD={_shq(recipe.workload)}
CNAME="{catalog.name}-$ALIAS"

if ! docker image inspect "$IMG" >/dev/null 2>&1; then
  echo ">> building shared role image $IMG ..."
  docker build -t "$IMG" --build-arg PUBKEY="$PUBKEY" - <<'DOCKERFILE'
{dockerfile.rstrip()}
DOCKERFILE
fi

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
docker rm -f "$CNAME" >/dev/null 2>&1 || true
docker run -d --name "$CNAME" --hostname "$ALIAS" --network "$NET" \\
    -e ROLE="$ROLE" -e WORKLOAD="$WORKLOAD" -p "127.0.0.1:$PORT:22" "$IMG" >/dev/null
echo ">> $ALIAS ($ROLE) up — 127.0.0.1:$PORT -> 22  [{recipe.title}]"
"""


def fleet_build_all_script(catalog: Catalog) -> str:
    """Bring the whole fleet up by invoking each per-recipe build script."""
    aliases = " ".join(r.alias for r in catalog.recipes)
    return f"""#!/usr/bin/env bash
# Bring up the entire {catalog.name} fleet ({len(catalog.recipes)} roles).
# Runs each per-recipe build script in this directory.
set -euo pipefail
: "${{PUBKEY:?export PUBKEY=\\"$(cat ~/.ssh/your_key.pub)\\" (the demo public key)}}"
HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
for a in {aliases}; do
  bash "$HERE/$a.sh"
done
echo ">> {catalog.name} fleet is up ({len(catalog.recipes)} hosts)."
"""
