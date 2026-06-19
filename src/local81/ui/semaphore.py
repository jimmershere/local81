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
