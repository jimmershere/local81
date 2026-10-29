from __future__ import annotations

import sqlite3
from argparse import Namespace
from pathlib import Path

from local81.config import validate_config
from local81.db.config import load_database_targets
from local81.db.redaction import redact_mapping
from local81.db.runner import run_database_command


def test_redacts_literal_secret_keys() -> None:
    assert redact_mapping({"password": "secret", "password_env": "DB_PASSWORD"}) == {"password": "<redacted>", "password_env": "DB_PASSWORD"}


def test_loads_ini_database_targets(tmp_path: Path) -> None:
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        """
[database "local"]
engine = sqlite
path = /tmp/app.db
tags = app,edge
password_env = DB_PASSWORD
""".strip(),
        encoding="utf-8",
    )
    targets = load_database_targets(cfg)
    assert len(targets) == 1
    assert targets[0].name == "local"
    assert targets[0].engine == "sqlite"
    assert targets[0].tags == ["app", "edge"]


def test_loads_db_target_with_valid_managed_ref(tmp_path: Path) -> None:
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        """
[database "local"]
engine = sqlite
path = /tmp/app.db
service_ref = bao://secret/prod/db#password
""".strip(),
        encoding="utf-8",
    )
    targets = load_database_targets(cfg)
    assert targets[0].settings["service_ref"] == "bao://secret/prod/db#password"


def test_rejects_malformed_managed_ref(tmp_path: Path) -> None:
    import pytest

    from local81.db.config import DatabaseConfigError

    cfg = tmp_path / "config.ini"
    cfg.write_text(
        """
[database "local"]
engine = sqlite
path = /tmp/app.db
service_ref = bao://missing-key-fragment
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(DatabaseConfigError):
        load_database_targets(cfg)


def test_plain_ref_value_is_not_treated_as_managed(tmp_path: Path) -> None:
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        """
[database "local"]
engine = sqlite
path = /tmp/app.db
service_ref = my-plain-service
""".strip(),
        encoding="utf-8",
    )
    targets = load_database_targets(cfg)
    assert targets[0].settings["service_ref"] == "my-plain-service"


def test_validate_config_rejects_literal_database_password(tmp_path: Path) -> None:
    local81 = tmp_path / ".local81"
    local81.mkdir()
    cfg = local81 / "config.ini"
    cfg.write_text(
        """
[local81]
version = 0.1
project = db-test
default_scope = main
state_dir = .local81/state
plans_dir = .local81/plans
runs_dir = .local81/runs
logs_dir = .local81/logs
lock_file = .local81/local81.lock
require_plan_for_deploy = true
fail_fast = true
max_parallel = 1
shell = /usr/bin/bash

[tools]
ssh = /usr/bin/ssh
rsync = /usr/bin/rsync
find = /usr/bin/find

[defaults]
rsync_opts = -az
backup = true
backup_suffix = .bkp
remote_mkdir = true
dry_run_default = false
log_hosts =
log_dest_dir = .local81/pulled-logs
jboss_log_path =
apache_log_path =

[routing]
env_from_filename_prefix = s:sys,q:qa,p:production
env_from_server_name_char_at = 4
env_from_server_name_char_map = s:sys,q:qa,p:production

[scope "main"]
enabled = true
source_dir = /tmp/source
target_dir = /tmp/target
servers = host1
discovery = mtime_since_last_success

[database "bad"]
engine = sqlite
path = /tmp/bad.db
password = literal
""".strip(),
        encoding="utf-8",
    )
    findings = validate_config(cfg)
    assert any("literal secret" in finding.detail for finding in findings)


def test_sqlite_db_doctor_writes_report(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as db:
        db.execute("create table sample(id integer primary key, name text)")
        db.execute("insert into sample(name) values ('ok')")
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        f"""
[database "app"]
engine = sqlite
path = {db_path}
""".strip(),
        encoding="utf-8",
    )
    out_dir = tmp_path / "artifacts"
    args = Namespace(db_command="doctor", config=str(cfg), target="app", engine=None, output_dir=str(out_dir), format="text", execute=False, quick=False, backup_path=None)
    monkeypatch.chdir(tmp_path)
    assert run_database_command(args) == 0
    summaries = list(out_dir.glob("*/summary.json"))
    assert summaries
    assert "sqlite:quick-check" in summaries[0].read_text(encoding="utf-8")


def test_sqlite_backup_requires_execute(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as db:
        db.execute("create table sample(id integer primary key)")
    cfg = tmp_path / "config.ini"
    cfg.write_text(
        f"""
[database "app"]
engine = sqlite
path = {db_path}
""".strip(),
        encoding="utf-8",
    )
    backup_path = tmp_path / "backup.db"
    args = Namespace(db_command="backup", config=str(cfg), target="app", engine=None, output_dir=str(tmp_path / "artifacts"), format="json", execute=True, quick=True, backup_path=str(backup_path))
    monkeypatch.chdir(tmp_path)
    assert run_database_command(args) == 0
    assert backup_path.exists()
