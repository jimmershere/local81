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


# --- catalog-driven rendering ----------------------------------------------

from local81.recipes import load_catalog  # noqa: E402
from local81.ui.n8n import render_n8n  # noqa: E402
from local81.ui.semaphore import render_semaphore_catalog  # noqa: E402
from local81.ui.stack import render_stack  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "recipes" / "fleet10.yaml"


def _catalog():
    return load_catalog(EXAMPLE)


def test_catalog_render_has_template_per_category_plus_per_recipe() -> None:
    c = _catalog()
    r = render_semaphore_catalog(c, db_host="pg17", db_password_ref="env://X")
    # one dispatcher template per category + one build template per recipe
    assert len(r.dispatchers) == len(c.categories)
    assert len(r.templates) == len(c.categories) + len(c.recipes)
    # backing-store posture is preserved through the catalog path
    assert r.base.config["postgres"]["password"] == "${SEMAPHORE_DB_PASSWORD}"
    assert r.base.findings == []


def test_catalog_render_surveys_have_action_dropdown() -> None:
    c = _catalog()
    r = render_semaphore_catalog(c, db_host="pg17", db_password_ref="env://X")
    deploy = next(t for t in r.templates if t["name"].endswith("Deploy & Release"))
    names = {v["name"] for v in deploy["survey_vars"]}
    assert "action" in names and "hosts" in names and "forks" in names
    action = next(v for v in deploy["survey_vars"] if v["name"] == "action")
    assert {opt["value"] for opt in action["values"]} == {"plan", "dry_run", "execute", "check", "rollback"}


def test_dispatcher_is_runnable_bash_with_no_secret() -> None:
    c = _catalog()
    r = render_semaphore_catalog(c, db_host="pg17", db_password_ref="env://X")
    deploy = r.dispatchers["dispatch/deploy.sh"]
    assert deploy.startswith("#!/usr/bin/env bash")
    assert "case \"$action\" in" in deploy
    assert "members()" in deploy
    # no DB password literal leaks into a script
    assert "SEMAPHORE_DB_PASSWORD" not in deploy


def test_n8n_one_workflow_per_category_is_expression() -> None:
    c = _catalog()
    wfs = render_n8n(c)
    assert len(wfs) == len(c.categories)
    deploy = wfs["local81-fleet10-deploy.workflow.json"]
    cmd = deploy["nodes"][1]["parameters"]["command"]
    assert cmd.startswith("=")  # n8n expression marker must lead the field
    assert "dispatch/deploy.sh" in cmd
    assert deploy["connections"]["Trigger"]["main"][0][0]["node"].endswith("dispatcher")


def test_stack_render_files_and_no_password_literal() -> None:
    c = _catalog()
    files = render_stack(c, db_password_ref="env://SEMAPHORE_DB_PASSWORD")
    assert "docker-compose.semaphore.yml" in files
    assert "launcher.html" in files
    assert "fleet/docker-compose.fleet.yml" in files
    # every recipe alias + port shows up in the generated fleet compose
    fleet = files["fleet/docker-compose.fleet.yml"]
    for rcp in c.recipes:
        assert rcp.alias in fleet and str(rcp.port) in fleet
    # the compose references the placeholder, never a literal value
    compose = files["docker-compose.semaphore.yml"]
    assert "${SEMAPHORE_DB_PASSWORD" in compose


def test_run_ui_n8n_and_stack_write_artifacts(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    rc = run_ui("n8n-render", catalog=str(EXAMPLE))
    assert rc == 0
    assert (tmp_path / ".local81" / "ui" / "n8n" / "local81-fleet10-deploy.workflow.json").is_file()
    capsys.readouterr()
    rc = run_ui("stack-render", catalog=str(EXAMPLE), db_password_ref="env://SEMAPHORE_DB_PASSWORD")
    assert rc == 0
    assert (tmp_path / ".local81" / "ui" / "stack" / "launcher.html").is_file()


def test_run_ui_stack_requires_password_ref(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    rc = run_ui("stack-render", catalog=str(EXAMPLE))
    assert rc == 1
    assert "db-password-ref" in capsys.readouterr().out
