from __future__ import annotations

import json
from pathlib import Path

import pytest

from local81.commands.ui import run_ui
from local81.ui.semaphore import KeyCustodyError, render_semaphore


# --- render core -----------------------------------------------------------

def test_default_mode_is_thin_and_clean() -> None:
    r = render_semaphore(db_host="pg17", db_password_ref="env://SEMAPHORE_DB_PASSWORD")
    assert r.key_custody == "local81"
    assert r.config["dialect"] == "postgres"
    assert r.config["postgres"]["options"]["sslmode"] == "verify-full"
    # no DB password literal anywhere
    assert r.config["postgres"]["password"] == "${SEMAPHORE_DB_PASSWORD}"
    assert "SEMAPHORE_DB_PASSWORD" in json.dumps(r.config)  # the placeholder/ref, not a value
    # templates shell out to the local81 CLI
    assert any(t["playbook"] == "local81 deploy --latest --execute" for t in r.templates)
    # the default mode does not move the posture
    assert r.findings == []


def test_db_engine_is_postgres17() -> None:
    r = render_semaphore(db_host="pg17", db_password_ref="env://X")
    assert r.config["_local81"]["db_engine"] == "postgres17"


def test_literal_db_password_is_rejected() -> None:
    with pytest.raises(ValueError):
        render_semaphore(db_host="pg17", db_password_ref="hunter2")


def test_weak_sslmode_flags_sc8() -> None:
    r = render_semaphore(db_host="pg17", db_password_ref="env://X", db_sslmode="disable")
    assert any("SC-8" in f.control for f in r.findings)


def test_native_keystore_refused_without_acceptance() -> None:
    with pytest.raises(KeyCustodyError):
        render_semaphore(db_host="pg17", db_password_ref="env://X", key_custody="semaphore")


def test_native_keystore_accepted_emits_l26_ui_001() -> None:
    r = render_semaphore(db_host="pg17", db_password_ref="env://X",
                         key_custody="semaphore", accept_key_custody=True)
    assert r.key_custody == "semaphore"
    assert any("L26-UI-001" in f.control for f in r.findings)


# --- command ---------------------------------------------------------------

def test_run_ui_writes_artifacts(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    rc = run_ui("semaphore-render", db_host="pg17.internal",
                db_password_ref="env://SEMAPHORE_DB_PASSWORD")
    out = capsys.readouterr().out
    assert rc == 0
    assert "PostgreSQL 17" in out
    base = tmp_path / ".local81" / "ui" / "semaphore"
    for name in ("semaphore-config.json", "local81-templates.json", "render.json"):
        assert (base / name).is_file()
    render = json.loads((base / "render.json").read_text(encoding="utf-8"))
    assert render["key_custody"] == "local81"
    assert render["findings"] == []


def test_run_ui_refuses_literal_password(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    rc = run_ui("semaphore-render", db_host="pg17", db_password_ref="literal-pw")
    out = capsys.readouterr().out
    assert rc == 1
    assert "secret reference" in out


def test_run_ui_mode1_requires_acceptance(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    rc = run_ui("semaphore-render", db_host="pg17", db_password_ref="env://X",
                key_custody="semaphore")
    assert rc == 1
    assert "accept_key_custody" in capsys.readouterr().out


def test_cli_parses_ui_semaphore_render() -> None:
    from local81.cli import build_parser

    args = build_parser().parse_args([
        "ui", "semaphore-render", "--db-host", "pg17", "--db-password-ref", "env://X",
        "--key-custody", "vault",
    ])
    assert args.command == "ui" and args.ui_command == "semaphore-render"
    assert args.db_host == "pg17" and args.key_custody == "vault"
