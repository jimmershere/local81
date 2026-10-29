from __future__ import annotations

import json
from pathlib import Path

from local81.commands.doctor import _plan_checks, _secrets_checks, run_doctor


def test_plan_checks_valid(tmp_path: Path) -> None:
    plan = tmp_path / "valid.plan.json"
    plan.write_text(json.dumps({
        "kind": "plan",
        "mode": "deploy",
        "schema": "local81.plan.v0.1",
        "plan_id": "p1",
        "scopes": [{"scope": "a", "steps": [{"id": "1"}]}],
    }), encoding="utf-8")
    results = _plan_checks(plan)
    by_name = {result.name: result.level for result in results}
    assert by_name["plan:json"] == "PASS"
    assert by_name["plan:kind"] == "PASS"
    assert by_name["plan:mode"] == "PASS"
    assert by_name["plan:schema_ver"] == "PASS"


def test_plan_checks_invalid_json(tmp_path: Path) -> None:
    plan = tmp_path / "broken.plan.json"
    plan.write_text("not json", encoding="utf-8")
    results = _plan_checks(plan)
    assert len(results) == 1
    assert results[0].name == "plan:json"
    assert results[0].level == "FAIL"


def test_secrets_checks_no_config_is_silent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert _secrets_checks() == []


def test_secrets_checks_counts_managed_refs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    local81 = tmp_path / ".local81"
    local81.mkdir()
    (local81 / "config.ini").write_text(
        """
[database "local"]
engine = sqlite
path = /tmp/app.db
service_ref = bao://secret/prod/db#password
""".strip(),
        encoding="utf-8",
    )
    results = _secrets_checks()
    assert results
    assert results[0].name == "secrets:refs"
    assert results[0].level == "PASS"
    assert "1 managed" in results[0].detail


def test_secrets_checks_reports_unparseable_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    local81 = tmp_path / ".local81"
    local81.mkdir()
    (local81 / "config.ini").write_text("[scope \"web\"]\na = 1\n[scope \"web\"]\na = 2\n", encoding="utf-8")
    results = _secrets_checks()
    assert len(results) == 1
    assert results[0].name == "secrets:refs"
    assert results[0].level == "FAIL"
    assert "could not parse" in results[0].detail


def test_run_doctor_warns_for_missing_project_dirs(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    rc = run_doctor()
    out = capsys.readouterr().out
    assert "dir:.local81" in out
    assert "[WARN]" in out
    assert rc in (0, 1)
