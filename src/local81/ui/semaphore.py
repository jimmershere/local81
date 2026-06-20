"""Render Semaphore UI configuration that fronts the ``local81`` CLI.

Semaphore (MIT, https://semaphoreui.com) is adopted as the batteries-included,
multi-user-RBAC web front end. We do *not* let it become local81: its task
templates shell out to the ``local81`` CLI on the control node, and its backing
store is standardized on **PostgreSQL 17** with TLS.

Key-custody modes — who holds the *target* SSH credentials:

* ``local81`` (default, "Mode 2") — Semaphore holds none; the CLI uses
  local81's own keys/secret backends. **No change to the secrets-never-at-rest
  posture beyond running any web app with its own login store.**
* ``vault`` ("Mode 1.5") — Semaphore pulls target creds from the same Vault /
  OpenBao local81 uses (``bao://``), so nothing durable lives in Semaphore.
* ``semaphore`` ("Mode 1") — Semaphore's native Key Store holds target SSH
  keys/sudo passwords, AES-encrypted at rest. This introduces target
  credentials at rest (outside local81's boundary), so it is **refused unless
  explicitly accepted** and it raises compliance finding ``L26-UI-001``.

This module only *renders* config; it never contacts Semaphore or a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..recipes import Catalog
from ..secrets import is_secret_ref

KEY_CUSTODY_MODES = ("local81", "vault", "semaphore")

# The local81 verbs Semaphore is wired to run, as task templates.
_TEMPLATES = (
    ("plan", "plan --ci"),
    ("deploy", "deploy --latest --execute"),
    ("deploy (dry-run)", "deploy --latest --dry-run"),
    ("rollback", "rollback {{ run_id }} --execute"),
    ("doctor --fleet", "doctor --fleet"),
)


@dataclass(frozen=True, slots=True)
class ComplianceFinding:
    control: str
    level: str
    detail: str

    def to_dict(self) -> dict:
        return {"control": self.control, "level": self.level, "detail": self.detail}


@dataclass(slots=True)
class RenderResult:
    config: dict[str, Any]
    templates: list[dict[str, Any]]
    key_custody: str
    findings: list[ComplianceFinding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema": "local81.ui.semaphore.v0.1",
            "key_custody": self.key_custody,
            "config": self.config,
            "templates": self.templates,
            "findings": [f.to_dict() for f in self.findings],
        }


class KeyCustodyError(Exception):
    """Raised when Mode 1 (native Key Store) is requested without acceptance."""


def render_semaphore(
    *,
    db_host: str,
    db_password_ref: str,
    db_port: int = 5432,
    db_name: str = "semaphore",
    db_user: str = "semaphore",
    db_sslmode: str = "verify-full",
    key_custody: str = "local81",
    accept_key_custody: bool = False,
    local81_bin: str = "local81",
    project_name: str = "local81",
) -> RenderResult:
    """Build the Semaphore project/template config. Pure; no I/O.

    ``db_password_ref`` must be a secret *reference* (never a literal): the
    rendered config carries a ``${SEMAPHORE_DB_PASSWORD}`` placeholder plus the
    reference for the operator to wire, so no DB password is ever written.
    """
    if key_custody not in KEY_CUSTODY_MODES:
        raise ValueError(f"key_custody must be one of {KEY_CUSTODY_MODES}, got {key_custody!r}")
    if not is_secret_ref(db_password_ref):
        raise ValueError(
            "db_password_ref must be a secret reference (env://, bao://, delinea://, ...), "
            f"never a literal: {db_password_ref!r}"
        )
    if key_custody == "semaphore" and not accept_key_custody:
        raise KeyCustodyError(
            "key_custody='semaphore' stores target SSH credentials at rest in "
            "Semaphore's database (outside local81's secrets-never-at-rest "
            "boundary). Re-run with accept_key_custody=True to take that on "
            "knowingly; prefer 'local81' (default) or 'vault'."
        )

    # Backing store: PostgreSQL 17, TLS-verified. Password is a placeholder env
    # the operator feeds from db_password_ref; access-key encryption likewise.
    config: dict[str, Any] = {
        "dialect": "postgres",
        "postgres": {
            "host": db_host,
            "port": db_port,
            "name": db_name,
            "user": db_user,
            "password": "${SEMAPHORE_DB_PASSWORD}",
            "options": {"sslmode": db_sslmode},
        },
        "access_key_encryption": "${SEMAPHORE_ACCESS_KEY_ENCRYPTION}",
        "_local81": {
            "db_engine": "postgres17",
            "db_password_ref": db_password_ref,
            "key_custody": key_custody,
            "note": "Secrets are placeholders fed from references at runtime; never literals.",
        },
    }

    templates = [
        {
            "name": f"{project_name}: {name}",
            "type": "bash",
            "playbook": f"{local81_bin} {cmd}".strip(),
            "description": f"Runs `{local81_bin} {cmd}` on the control node.",
        }
        for name, cmd in _TEMPLATES
    ]

    result = RenderResult(config=config, templates=templates, key_custody=key_custody)
    result.findings.extend(_compliance_findings(key_custody, db_sslmode))
    return result


def _compliance_findings(key_custody: str, db_sslmode: str) -> list[ComplianceFinding]:
    findings: list[ComplianceFinding] = []
    if key_custody == "semaphore":
        findings.append(ComplianceFinding(
            control="L26-UI-001 (NIST SP 800-53 IA-5, SC-28)",
            level="WARN",
            detail=("Semaphore Key Store holds target SSH credentials at rest "
                    "(AES-encrypted) — outside local81's secrets-never-at-rest "
                    "boundary. Acceptable only with explicit operator sign-off; "
                    "prefer key_custody=local81 or vault."),
        ))
    if db_sslmode not in ("require", "verify-ca", "verify-full"):
        findings.append(ComplianceFinding(
            control="L26-UI-002 (NIST SP 800-53 SC-8)",
            level="WARN",
            detail=(f"Semaphore<->Postgres sslmode={db_sslmode!r} is not encrypted/"
                    "verified; use verify-full for transmission confidentiality."),
        ))
    return findings


# --- catalog-driven rendering ----------------------------------------------
# When a recipe catalog is supplied, the five hardcoded templates above are
# replaced by *survey-driven* templates: one Semaphore template per action
# category, each prompting with dropdowns (action + host group + parameters) and
# shelling out to a generated dispatcher script. That dispatcher is the "complex
# thing behind the easy button" — the operator picks from menus and clicks Run;
# the script turns the choices into the right `local81 ...` invocation. Plus one
# build template per recipe, for standing the workload's image up.


@dataclass(slots=True)
class CatalogRender:
    """Everything the Semaphore surface needs, projected from one catalog."""

    base: RenderResult
    templates: list[dict[str, Any]]
    dispatchers: dict[str, str]  # relative path -> script text

    def to_dict(self) -> dict:
        d = self.base.to_dict()
        d["templates"] = self.templates
        d["dispatchers"] = sorted(self.dispatchers)
        return d


def render_semaphore_catalog(
    catalog: Catalog,
    *,
    db_host: str,
    db_password_ref: str,
    db_port: int = 5432,
    db_name: str = "semaphore",
    db_user: str = "semaphore",
    db_sslmode: str = "verify-full",
    key_custody: str = "local81",
    accept_key_custody: bool = False,
    local81_bin: str = "local81",
) -> CatalogRender:
    """Project a catalog into Semaphore templates + dispatcher scripts.

    Reuses :func:`render_semaphore` for the backing-store config and compliance
    findings, then overrides its placeholder templates with catalog-driven ones.
    """
    base = render_semaphore(
        db_host=db_host, db_password_ref=db_password_ref, db_port=db_port,
        db_name=db_name, db_user=db_user, db_sslmode=db_sslmode,
        key_custody=key_custody, accept_key_custody=accept_key_custody,
        local81_bin=local81_bin, project_name=catalog.name,
    )

    templates: list[dict[str, Any]] = []
    dispatchers: dict[str, str] = {}

    for cat in catalog.categories:
        script_path = f"dispatch/{cat.key}.sh"
        dispatchers[script_path] = _dispatcher_script(catalog, cat, local81_bin)
        templates.append({
            "name": f"{catalog.name}: {cat.title}",
            "type": "bash",
            "playbook": script_path,
            "description": f"Pick an action and a host group; runs the matching `{local81_bin}` command.",
            "survey_vars": _survey_vars(catalog, cat),
        })

    # One build template per recipe — stands the role's image/workload up.
    for r in catalog.recipes:
        templates.append({
            "name": f"{catalog.name} build: {r.alias} ({r.title})",
            "type": "bash",
            "playbook": f"build/{r.alias}.sh",
            "description": f"Build + launch the {r.role} workload ({r.title}) on {r.alias}.",
            "survey_vars": [],
        })

    # Replace the base's placeholder templates with the catalog-driven set.
    base.config["_local81"]["catalog"] = catalog.name
    return CatalogRender(base=base, templates=templates, dispatchers=dispatchers)


def _survey_vars(catalog: Catalog, cat) -> list[dict[str, Any]]:
    """Build the dropdowns/fields an operator sees for one category template."""
    vars_: list[dict[str, Any]] = [
        {
            "name": "action",
            "title": "Action",
            "type": "enum",
            "required": True,
            "values": [{"name": o.label, "value": o.key} for o in cat.options],
        }
    ]
    # The host-group dropdown only bites for deploy-family actions; surfaced here
    # so the choice is one click, and ignored honestly by control-node actions.
    if cat.key == "deploy":
        vars_.append({
            "name": "hosts",
            "title": "Which hosts",
            "type": "enum",
            "required": True,
            "values": [{"name": g.label, "value": g.key} for g in catalog.groups],
        })
    # One field per parameter actually referenced by this category's options.
    used: list[str] = []
    for o in cat.options:
        for name in o.placeholders():
            if name not in used:
                used.append(name)
    for name in used:
        p = catalog.parameters[name]
        var: dict[str, Any] = {"name": p.key, "title": p.key, "type": p.type, "default": str(p.default)}
        if p.type == "enum":
            var["values"] = [{"name": c, "value": c} for c in p.choices]
        vars_.append(var)
    return vars_


def _dispatcher_script(catalog: Catalog, cat, local81_bin: str) -> str:
    """Generate the bash dispatcher that turns survey choices into a command."""
    # Parameters this category reads from the environment (Semaphore exposes
    # survey vars as env), each defaulted so the script also runs by hand.
    used: list[str] = []
    for o in cat.options:
        for name in o.placeholders():
            if name not in used:
                used.append(name)
    param_lines = "\n".join(
        f'{name}="${{{name}:-{catalog.parameters[name].default}}}"' for name in used
    )

    # case arm per action: {param} -> "${param}" (quoted env ref), tokenized.
    arms = []
    for o in cat.options:
        tokens = []
        for tok in o.command.split():
            if tok.startswith("{") and tok.endswith("}"):
                tokens.append(f'"${{{tok[1:-1]}}}"')
            else:
                tokens.append(tok)
        arms.append(f'  {o.key}) cmd=({" ".join(tokens)}) ;;')
    arms_block = "\n".join(arms)

    # members() for host scoping, generated from the catalog's groups.
    member_arms = "\n".join(
        f'    {g.key}) echo "{" ".join(catalog.group_aliases(g.key))}" ;;'
        for g in catalog.groups
    )

    return f"""#!/usr/bin/env bash
# Rendered by `local81 ui semaphore-render` from catalog '{catalog.name}'
# (category: {cat.title}). Semaphore passes survey variables as environment
# variables; this script maps the chosen action to a `{local81_bin}` command.
# Safe to run by hand too: every variable has a default.
set -euo pipefail
LOCAL81="${{LOCAL81_BIN:-{local81_bin}}}"
action="${{action:-}}"
hosts="${{hosts:-all}}"
{param_lines}

members() {{
  case "$1" in
{member_arms}
    *) echo "" ;;
  esac
}}

declare -a cmd
case "$action" in
{arms_block}
  *) echo "unknown action: $action" >&2; exit 2 ;;
esac

# Host scoping only applies to deploy-family actions; everything else runs once
# on the control node and ignores the host group.
if [[ "${{cmd[0]}}" == "deploy" && "$hosts" != "all" ]]; then
  for h in $(members "$hosts"); do
    echo ">> $LOCAL81 ${{cmd[*]}} --limit $h"
    "$LOCAL81" "${{cmd[@]}}" --limit "$h"
  done
else
  echo ">> $LOCAL81 ${{cmd[*]}}"
  "$LOCAL81" "${{cmd[@]}}"
fi
"""
