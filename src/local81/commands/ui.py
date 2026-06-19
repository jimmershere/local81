"""``local81 ui semaphore-render`` — emit Semaphore UI config that fronts the CLI.

Writes operator-installable Semaphore config under ``.local81/ui/semaphore/``:
the runtime config (PostgreSQL 17 + TLS, secrets as placeholders), the task
templates that shell out to ``local81``, and a render manifest carrying any
compliance findings. It is purely a renderer — it never contacts Semaphore.
"""

from __future__ import annotations

import json
from pathlib import Path

from local81.ui.semaphore import KeyCustodyError, render_semaphore


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)


def run_ui(action: str, *, db_host: str | None = None, db_password_ref: str | None = None,
           db_port: int = 5432, db_name: str = "semaphore", db_user: str = "semaphore",
           db_sslmode: str = "verify-full", key_custody: str = "local81",
           accept_key_custody: bool = False, local81_bin: str = "local81",
           out_dir: str | None = None) -> int:
    if action != "semaphore-render":
        print(f"Local-81 ui: unknown action {action!r}")
        return 2
    if not db_host:
        print("Local-81 ui semaphore-render: --db-host is required (the PostgreSQL 17 host).")
        return 1
    if not db_password_ref:
        print("Local-81 ui semaphore-render: --db-password-ref is required (a secret reference, never a literal).")
        return 1

    try:
        result = render_semaphore(
            db_host=db_host, db_password_ref=db_password_ref, db_port=db_port,
            db_name=db_name, db_user=db_user, db_sslmode=db_sslmode,
            key_custody=key_custody, accept_key_custody=accept_key_custody,
            local81_bin=local81_bin,
        )
    except KeyCustodyError as exc:
        print(f"[fail] {exc}")
        return 1
    except ValueError as exc:
        print(f"[fail] {exc}")
        return 1

    base = Path(out_dir) if out_dir else Path(".local81") / "ui" / "semaphore"
    _write(base / "semaphore-config.json", result.config)
    _write(base / "local81-templates.json", {"templates": result.templates})
    _write(base / "render.json", result.to_dict())

    print("Local-81 ui semaphore-render")
    print("============================")
    print(f"Backing store: PostgreSQL 17 @ {db_host}:{db_port}/{db_name} (sslmode={db_sslmode})")
    print(f"Key custody:   {key_custody}")
    print(f"Templates:     {len(result.templates)} (wired to `{local81_bin} ...`)")
    print(f"Written to:    {base}/\n")
    for finding in result.findings:
        print(f"[{finding.level.lower()}] {finding.control}: {finding.detail}")
    if not result.findings:
        print("No compliance findings — this configuration does not move the posture.")
    print("\nNext: set SEMAPHORE_DB_PASSWORD (from your secret ref) and "
          "SEMAPHORE_ACCESS_KEY_ENCRYPTION in Semaphore's environment, then import the templates.")
    return 0
