"""``local81 ui ...`` — render config/scaffolding for surfaces that front the CLI.

Three actions, all driven (optionally) by a recipe catalog so one data file is
the single source of truth for every surface:

* ``semaphore-render`` — Semaphore project config + task templates. With
  ``--catalog`` the five placeholder templates become survey-driven, one per
  action category, each shelling out to a generated dispatcher script.
* ``n8n-render`` — one importable n8n workflow per category (reuses the same
  dispatcher scripts).
* ``stack-render`` — a *runnable* Semaphore + PostgreSQL 17 stack, a friendly
  launcher page, and the catalog's fleet compose.

It is purely a renderer — it never contacts Semaphore, n8n, a database, or a
target host, and it never writes a secret literal.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from local81.recipes import CatalogError, load_catalog
from local81.ui.n8n import render_n8n
from local81.ui.semaphore import KeyCustodyError, render_semaphore, render_semaphore_catalog
from local81.ui.stack import render_stack

_UI_ACTIONS = ("semaphore-render", "n8n-render", "stack-render")


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)


# Brand assets the launcher uses (the union seal + the Clem emblem). Best-effort:
# copied from the source tree's docs/assets if present (editable installs have
# them), skipped silently otherwise — the launcher degrades gracefully.
_BRAND_ASSETS = ("local81-logo.svg", "local81-emblem.png")


def _copy_brand_assets(dest: Path) -> list[str]:
    src = Path(__file__).resolve().parents[3] / "docs" / "assets"
    copied: list[str] = []
    if not src.is_dir():
        return copied
    dest.mkdir(parents=True, exist_ok=True)
    try:
        dest.chmod(0o700)
    except OSError:
        pass
    for name in _BRAND_ASSETS:
        s = src / name
        if s.is_file():
            try:
                shutil.copyfile(s, dest / name)
                (dest / name).chmod(0o644)
                copied.append(name)
            except OSError:
                pass
    return copied


def _write_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def run_ui(action: str, *, db_host: str | None = None, db_password_ref: str | None = None,
           db_port: int = 5432, db_name: str = "semaphore", db_user: str = "semaphore",
           db_sslmode: str = "verify-full", key_custody: str = "local81",
           accept_key_custody: bool = False, local81_bin: str = "local81",
           out_dir: str | None = None, catalog: str | None = None) -> int:
    if action not in _UI_ACTIONS:
        print(f"Local-81 ui: unknown action {action!r} (expected one of {', '.join(_UI_ACTIONS)})")
        return 2

    cat = None
    if catalog:
        try:
            cat = load_catalog(catalog)
        except CatalogError as exc:
            print(f"[fail] {exc}")
            return 1

    if action == "semaphore-render":
        return _run_semaphore(
            db_host=db_host, db_password_ref=db_password_ref, db_port=db_port,
            db_name=db_name, db_user=db_user, db_sslmode=db_sslmode,
            key_custody=key_custody, accept_key_custody=accept_key_custody,
            local81_bin=local81_bin, out_dir=out_dir, cat=cat,
        )
    if action == "n8n-render":
        return _run_n8n(cat, out_dir=out_dir)
    if action == "stack-render":
        return _run_stack(cat, db_password_ref=db_password_ref, out_dir=out_dir)
    return 2  # unreachable


def _run_semaphore(*, db_host, db_password_ref, db_port, db_name, db_user, db_sslmode,
                   key_custody, accept_key_custody, local81_bin, out_dir, cat) -> int:
    if not db_host:
        print("Local-81 ui semaphore-render: --db-host is required (the PostgreSQL 17 host).")
        return 1
    if not db_password_ref:
        print("Local-81 ui semaphore-render: --db-password-ref is required (a secret reference, never a literal).")
        return 1

    base = Path(out_dir) if out_dir else Path(".local81") / "ui" / "semaphore"
    try:
        if cat is not None:
            render = render_semaphore_catalog(
                cat, db_host=db_host, db_password_ref=db_password_ref, db_port=db_port,
                db_name=db_name, db_user=db_user, db_sslmode=db_sslmode,
                key_custody=key_custody, accept_key_custody=accept_key_custody,
                local81_bin=local81_bin,
            )
            result = render.base
            _write(base / "semaphore-config.json", result.config)
            _write(base / "templates.json", {"templates": render.templates})
            _write(base / "render.json", render.to_dict())
            for rel, text in render.scripts().items():
                _write_text(base / rel, text, mode=0o700)
            templ_count = len(render.templates)
            extra = f"Catalog:       {cat.name} ({len(cat.recipes)} recipes, {len(cat.categories)} categories)"
            dispatch = (f"Scripts:       {len(render.dispatchers)} dispatcher(s) + "
                        f"{len(render.build_scripts)} build script(s) under {base}/")
        else:
            result = render_semaphore(
                db_host=db_host, db_password_ref=db_password_ref, db_port=db_port,
                db_name=db_name, db_user=db_user, db_sslmode=db_sslmode,
                key_custody=key_custody, accept_key_custody=accept_key_custody,
                local81_bin=local81_bin,
            )
            _write(base / "semaphore-config.json", result.config)
            _write(base / "local81-templates.json", {"templates": result.templates})
            _write(base / "render.json", result.to_dict())
            templ_count = len(result.templates)
            extra = "Catalog:       (none — pass --catalog for survey-driven templates)"
            dispatch = ""
    except KeyCustodyError as exc:
        print(f"[fail] {exc}")
        return 1
    except ValueError as exc:
        print(f"[fail] {exc}")
        return 1

    print("Local-81 ui semaphore-render")
    print("============================")
    print(f"Backing store: PostgreSQL 17 @ {db_host}:{db_port}/{db_name} (sslmode={db_sslmode})")
    print(f"Key custody:   {key_custody}")
    print(f"Templates:     {templ_count}")
    print(extra)
    if dispatch:
        print(dispatch)
    print(f"Written to:    {base}/\n")
    for finding in result.findings:
        print(f"[{finding.level.lower()}] {finding.control}: {finding.detail}")
    if not result.findings:
        print("No compliance findings — this configuration does not move the posture.")
    print("\nNext: set SEMAPHORE_DB_PASSWORD (from your secret ref) and "
          "SEMAPHORE_ACCESS_KEY_ENCRYPTION in Semaphore's environment, then import the templates.")
    return 0


def _run_n8n(cat, *, out_dir) -> int:
    if cat is None:
        print("Local-81 ui n8n-render: --catalog is required (the recipe catalog to project).")
        return 1
    base = Path(out_dir) if out_dir else Path(".local81") / "ui" / "n8n"
    workflows = render_n8n(cat)
    for name, wf in workflows.items():
        _write(base / name, wf)
    print("Local-81 ui n8n-render")
    print("======================")
    print(f"Catalog:    {cat.name}")
    print(f"Workflows:  {len(workflows)} (one per category) -> {base}/")
    for name in sorted(workflows):
        print(f"  - {name}")
    print("\nImport each via n8n -> Import from File. Each webhook shells the matching "
          "dispatch/<category>.sh, so n8n and Semaphore run identical local81 commands.")
    return 0


def _run_stack(cat, *, db_password_ref, out_dir) -> int:
    if cat is None:
        print("Local-81 ui stack-render: --catalog is required (the recipe catalog to project).")
        return 1
    if not db_password_ref:
        print("Local-81 ui stack-render: --db-password-ref is required (a secret reference, never a literal).")
        return 1
    base = Path(out_dir) if out_dir else Path(".local81") / "ui" / "stack"
    files = render_stack(cat, db_password_ref=db_password_ref)
    for rel, text in files.items():
        mode = 0o700 if rel.endswith(".sh") else 0o600
        _write_text(base / rel, text, mode=mode)
    copied = _copy_brand_assets(base / "assets")
    print("Local-81 ui stack-render")
    print("========================")
    print(f"Catalog:   {cat.name} ({len(cat.recipes)} recipes)")
    print(f"Written:   {len(files)} files -> {base}/")
    if copied:
        print(f"Brand:     {len(copied)} asset(s) -> {base}/assets/ ({', '.join(copied)})")
    for rel in sorted(files):
        print(f"  - {rel}")
    print("\nNext: cp .env.example .env and fill it from your secret references, then "
          "`docker compose -f docker-compose.semaphore.yml --env-file .env up -d`. "
          "Open launcher.html for the friendly view.")
    return 0
