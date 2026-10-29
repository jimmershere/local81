from __future__ import annotations

import io
import json
import os
from configparser import ConfigParser

from local81.commands.guided import (
    GuidedAnswers,
    _normalize_servers,
    generate_config,
    generate_config_yaml,
    preview_config,
    run_guided,
    run_guided_interview,
)


# ---------------------------------------------------------------------------
# Unit: server normalization
# ---------------------------------------------------------------------------

def test_normalize_servers_comma():
    assert _normalize_servers("host1,host2,host3") == "host1,host2,host3"


def test_normalize_servers_spaces():
    assert _normalize_servers("host1 host2 host3") == "host1,host2,host3"


def test_normalize_servers_mixed():
    assert _normalize_servers("host1, host2,  host3  host4") == "host1,host2,host3,host4"


def test_normalize_servers_dedup():
    assert _normalize_servers("host1,host2,host1,host3") == "host1,host2,host3"


def test_normalize_servers_whitespace_only():
    assert _normalize_servers("   ") == ""


# ---------------------------------------------------------------------------
# Unit: config generation
# ---------------------------------------------------------------------------

def test_generate_config_has_all_sections():
    answers = GuidedAnswers(
        project="myproj",
        scope_name="main",
        source_dir="/src",
        target_dir="/dst",
        servers="host1,host2",
    )
    config_text = generate_config(answers)
    cp = ConfigParser()
    cp.read_string(config_text)
    assert "local81" in cp
    assert "tools" in cp
    assert "defaults" in cp
    assert "routing" in cp
    assert 'scope "main"' in cp


def test_generate_config_values():
    answers = GuidedAnswers(
        project="testproj",
        scope_name="deploy",
        source_dir="/app/src",
        target_dir="/opt/deploy",
        servers="web1,web2",
        rsync_opts="-az --delete",
        fail_fast=False,
        backup=True,
        backup_suffix=".old",
        max_parallel=2,
        remote_mkdir=False,
    )
    config_text = generate_config(answers)
    cp = ConfigParser()
    cp.read_string(config_text)
    assert cp["local81"]["project"] == "testproj"
    assert cp["local81"]["default_scope"] == "deploy"
    assert cp["local81"]["fail_fast"] == "false"
    assert cp["local81"]["max_parallel"] == "2"
    assert cp["defaults"]["backup"] == "true"
    assert cp["defaults"]["backup_suffix"] == ".old"
    assert cp["defaults"]["remote_mkdir"] == "false"
    assert cp["defaults"]["rsync_opts"] == "-az --delete"
    scope = cp['scope "deploy"']
    assert scope["source_dir"] == "/app/src"
    assert scope["target_dir"] == "/opt/deploy"
    assert scope["servers"] == "web1,web2"
    assert scope["rsync_opts"] == "-az --delete"


def test_generate_config_no_backup():
    answers = GuidedAnswers(
        project="p", scope_name="s", source_dir="/s", target_dir="/t",
        servers="h1", backup=False,
    )
    config_text = generate_config(answers)
    cp = ConfigParser()
    cp.read_string(config_text)
    assert cp["defaults"]["backup"] == "false"
    assert cp['scope "s"']["backup"] == "false"


def test_generate_config_log_hosts():
    answers = GuidedAnswers(
        project="p", scope_name="s", source_dir="/s", target_dir="/t",
        servers="h1", log_hosts="log1,log2", jboss_log_path="/var/log/jboss",
    )
    config_text = generate_config(answers)
    cp = ConfigParser()
    cp.read_string(config_text)
    assert cp["defaults"]["log_hosts"] == "log1,log2"
    assert cp["defaults"]["jboss_log_path"] == "/var/log/jboss"


def test_generate_config_constant_defaults():
    answers = GuidedAnswers(
        project="p", scope_name="main", source_dir="/s", target_dir="/t",
        servers="h1",
    )
    config_text = generate_config(answers)
    cp = ConfigParser()
    cp.read_string(config_text)
    assert cp["local81"]["version"] == "0.1"
    assert cp["local81"]["state_dir"] == ".local81/state"
    assert cp["local81"]["shell"] == "/usr/bin/bash"
    assert cp["tools"]["ssh"] == "/usr/bin/ssh"
    assert cp["tools"]["rsync"] == "/usr/bin/rsync"
    assert cp["defaults"]["rsync_opts"] == "-az"
    assert cp["routing"]["env_from_server_name_char_at"] == "4"
    scope = cp['scope "main"']
    assert scope["enabled"] == "true"
    assert scope["discovery"] == "mtime_since_last_success"


def test_generate_config_yaml_matches_answers():
    answers = GuidedAnswers(
        project="yamlproj",
        scope_name="main",
        source_dir="/srv/app",
        target_dir="/opt/app",
        servers="web1,web2",
        rsync_opts="-az --delete",
    )
    payload = generate_config_yaml(answers)
    assert "project: yamlproj" in payload
    assert "default_scope: main" in payload
    assert "rsync_opts: -az --delete" in payload
    assert "- web1" in payload


# ---------------------------------------------------------------------------
# Integration: interview flow with simulated input
# ---------------------------------------------------------------------------

def _make_input(lines: list[str]) -> io.StringIO:
    return io.StringIO("\n".join(lines) + "\n")


def test_interview_all_defaults(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    inp = _make_input([
        "",                      # project name -> cwd name
        "",                      # scope name -> main
        str(source_dir),          # source dir
        "/opt/target",           # target dir
        "y",                     # remote mkdir
        "web1,web2",             # servers
        "",                      # rsync opts -> -az
        "y",                     # fail fast
        "y",                     # backup
        "",                      # backup suffix -> .bkp
        "",                      # max parallel -> 4
        "n",                     # log hosts -> skip
    ])
    out = io.StringIO()
    original = os.getcwd()
    try:
        os.chdir(tmp_path)
        answers = run_guided_interview(inp=inp, out=out)
    finally:
        os.chdir(original)
    assert answers is not None
    assert answers.scope_name == "main"
    assert answers.source_dir == str(source_dir)
    assert answers.target_dir == "/opt/target"
    assert answers.servers == "web1,web2"
    assert answers.rsync_opts == "-az"
    assert answers.fail_fast is True
    assert answers.backup is True
    assert answers.backup_suffix == ".bkp"
    assert answers.max_parallel == 4
    assert answers.log_hosts == ""


def test_interview_custom_values(tmp_path):
    source_dir = tmp_path / "deploy-src"
    source_dir.mkdir()
    inp = _make_input([
        "myproject",             # project name
        "staging",               # scope name
        str(source_dir),          # source dir
        "/opt/staging",          # target dir
        "n",                     # remote mkdir
        "app1 app2 app3",        # servers
        "-az --delete",          # rsync opts
        "n",                     # fail fast
        "y",                     # backup
        ".backup",               # backup suffix
        "2",                     # max parallel
        "y",                     # want log hosts
        "loghost1",              # log hosts
        "y",                     # want log paths
        "/var/log/jboss",        # jboss
        "/var/log/apache",       # apache
    ])
    out = io.StringIO()
    original = os.getcwd()
    try:
        os.chdir(tmp_path)
        answers = run_guided_interview(inp=inp, out=out)
    finally:
        os.chdir(original)
    assert answers is not None
    assert answers.project == "myproject"
    assert answers.scope_name == "staging"
    assert answers.remote_mkdir is False
    assert answers.servers == "app1,app2,app3"
    assert answers.rsync_opts == "-az --delete"
    assert answers.fail_fast is False
    assert answers.backup_suffix == ".backup"
    assert answers.max_parallel == 2
    assert answers.log_hosts == "loghost1"
    assert answers.jboss_log_path == "/var/log/jboss"
    assert answers.apache_log_path == "/var/log/apache"


def test_interview_empty_source_retry_then_provide():
    inp = _make_input([
        "",              # project name -> default
        "",              # scope name -> main
        "",              # source dir -> empty (first try)
        "/real/source",  # source dir -> retry
        "y",             # continue with placeholder source
        "/opt/target",   # target dir
        "y",             # remote mkdir
        "host1",         # servers
        "",              # rsync opts
        "y",             # fail fast
        "y",             # backup
        "",              # backup suffix
        "",              # max parallel
        "n",             # log hosts
    ])
    out = io.StringIO()
    answers = run_guided_interview(inp=inp, out=out)
    assert answers is not None
    assert answers.source_dir == "/real/source"


def test_interview_empty_source_both_tries_returns_none():
    inp = _make_input([
        "",   # project name
        "",   # scope name
        "",   # source dir empty
        "",   # source dir retry also empty
    ])
    out = io.StringIO()
    answers = run_guided_interview(inp=inp, out=out)
    assert answers is None


def test_interview_empty_target_both_tries_returns_none():
    inp = _make_input([
        "",              # project name
        "",              # scope name
        "/src",          # source dir
        "y",             # keep placeholder source
        "",              # target dir empty
        "",              # target dir retry also empty
    ])
    out = io.StringIO()
    answers = run_guided_interview(inp=inp, out=out)
    assert answers is None


def test_interview_empty_servers_both_tries_returns_none():
    inp = _make_input([
        "",              # project name
        "",              # scope name
        "/src",          # source dir
        "y",             # keep placeholder source
        "/dst",          # target dir
        "y",             # remote mkdir
        "",              # servers empty
        "",              # servers retry also empty
    ])
    out = io.StringIO()
    answers = run_guided_interview(inp=inp, out=out)
    assert answers is None


def test_interview_server_dedup():
    inp = _make_input([
        "",                      # project name
        "",                      # scope name
        "/definitely/missing/src",  # source dir
        "y",                        # keep placeholder source
        "/dst",                  # target dir
        "y",                     # remote mkdir
        "host1,host2,host1",     # servers with dupe
        "",                      # rsync opts
        "y",                     # fail fast
        "y",                     # backup
        "",                      # suffix
        "",                      # parallel
        "n",                     # log hosts
    ])
    out = io.StringIO()
    answers = run_guided_interview(inp=inp, out=out)
    assert answers is not None
    assert answers.servers == "host1,host2"


# ---------------------------------------------------------------------------
# Integration: full run_guided writes files
# ---------------------------------------------------------------------------

def test_run_guided_writes_config(tmp_path):
    source_dir = tmp_path / "app-src"
    source_dir.mkdir()
    inp = _make_input([
        "testproj",              # project
        "main",                  # scope
        str(source_dir),          # source
        "/opt/dst",              # target
        "y",                     # remote mkdir
        "web1,web2",             # servers
        "-az --delete",          # rsync opts
        "y",                     # fail fast
        "y",                     # backup
        "",                      # suffix
        "",                      # parallel
        "n",                     # log hosts
        "write",                 # confirm write
    ])
    out = io.StringIO()
    original = os.getcwd()
    try:
        os.chdir(tmp_path)
        rc = run_guided(force=False, inp=inp, out=out)
    finally:
        os.chdir(original)

    assert rc == 0
    config_ini_path = tmp_path / ".local81" / "config.ini"
    config_yaml_path = tmp_path / ".local81" / "config.yaml"
    assert config_ini_path.exists()
    assert config_yaml_path.exists()

    cp = ConfigParser()
    cp.read(str(config_ini_path))
    assert cp["local81"]["project"] == "testproj"
    assert 'scope "main"' in cp
    assert cp['scope "main"']["servers"] == "web1,web2"
    assert cp['scope "main"']["rsync_opts"] == "-az --delete"

    # State file written
    state_file = tmp_path / ".local81" / "state" / "main.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert state["scope"] == "main"
    assert state["schema"] == "local81.state.v0.1"

    # Directories created
    assert (tmp_path / ".local81" / "plans").is_dir()
    assert (tmp_path / ".local81" / "runs").is_dir()
    assert (tmp_path / ".local81" / "logs").is_dir()


def test_run_guided_cancel(tmp_path):
    inp = _make_input([
        "",              # project
        "",              # scope
        "/src",          # source
        "y",             # keep placeholder source
        "/dst",          # target
        "y",             # remote mkdir
        "host1",         # servers
        "",              # rsync opts
        "y",             # fail fast
        "y",             # backup
        "",              # suffix
        "",              # parallel
        "n",             # log hosts
        "cancel",        # cancel write
    ])
    out = io.StringIO()
    original = os.getcwd()
    try:
        os.chdir(tmp_path)
        rc = run_guided(force=False, inp=inp, out=out)
    finally:
        os.chdir(original)

    assert rc == 1
    assert not (tmp_path / ".local81" / "config.ini").exists()
    assert not (tmp_path / ".local81" / "config.yaml").exists()
    assert "Cancelled" in out.getvalue()


def test_run_guided_existing_config_without_force(tmp_path):
    (tmp_path / ".local81").mkdir()
    (tmp_path / ".local81" / "config.ini").write_text("[local81]\n")
    out = io.StringIO()
    inp = io.StringIO("")
    original = os.getcwd()
    try:
        os.chdir(tmp_path)
        rc = run_guided(force=False, inp=inp, out=out)
    finally:
        os.chdir(original)
    assert rc == 1
    assert "already exists" in out.getvalue()


def test_run_guided_existing_yaml_without_force(tmp_path):
    (tmp_path / ".local81").mkdir()
    (tmp_path / ".local81" / "config.yaml").write_text("local81: {}\n")
    out = io.StringIO()
    inp = io.StringIO("")
    original = os.getcwd()
    try:
        os.chdir(tmp_path)
        rc = run_guided(force=False, inp=inp, out=out)
    finally:
        os.chdir(original)
    assert rc == 1
    assert "already exists" in out.getvalue()


def test_run_guided_existing_config_with_force(tmp_path):
    (tmp_path / ".local81").mkdir()
    (tmp_path / ".local81" / "config.ini").write_text("[local81]\n")
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    inp = _make_input([
        "forced",                # project
        "prod",                  # scope
        str(source_dir),          # source
        "/dst",                  # target
        "y",                     # remote mkdir
        "host1",                 # servers
        "",                      # rsync opts
        "y",                     # fail fast
        "n",                     # no backup
        "",                      # parallel
        "n",                     # log hosts
        "write",                 # confirm
    ])
    out = io.StringIO()
    original = os.getcwd()
    try:
        os.chdir(tmp_path)
        rc = run_guided(force=True, inp=inp, out=out)
    finally:
        os.chdir(original)
    assert rc == 0
    cp = ConfigParser()
    cp.read(str(tmp_path / ".local81" / "config.ini"))
    assert cp["local81"]["project"] == "forced"


def test_run_guided_config_parseable_by_local81_config_loader(tmp_path):
    source_dir = tmp_path / "app-src"
    source_dir.mkdir()
    inp = _make_input([
        "integtest",             # project
        "main",                  # scope
        str(source_dir),          # source
        "/opt/dst",              # target
        "y",                     # remote mkdir
        "svr1,svr2",             # servers
        "",                      # rsync opts
        "y",                     # fail fast
        "y",                     # backup
        "",                      # suffix
        "1",                     # parallel
        "n",                     # log hosts
        "write",                 # confirm
    ])
    out = io.StringIO()
    original = os.getcwd()
    try:
        os.chdir(tmp_path)
        rc = run_guided(force=False, inp=inp, out=out)
    finally:
        os.chdir(original)
    assert rc == 0

    from local81.config import load_config

    cfg = load_config(tmp_path / ".local81" / "config.ini")
    assert cfg.project == "integtest"
    assert len(cfg.scopes) == 1
    assert cfg.scopes[0].name == "main"
    assert str(cfg.scopes[0].source_dir) == str(source_dir)
    assert cfg.scopes[0].servers == ["svr1", "svr2"]


def test_yaml_config_parseable_by_local81_config_loader(tmp_path):
    source_dir = tmp_path / "app-src"
    source_dir.mkdir()
    inp = _make_input([
        "yamltest",              # project
        "main",                  # scope
        str(source_dir),          # source
        "/opt/dst",              # target
        "y",                     # remote mkdir
        "svr1,svr2",             # servers
        "",                      # rsync opts
        "y",                     # fail fast
        "y",                     # backup
        "",                      # suffix
        "1",                     # parallel
        "n",                     # log hosts
        "write",                 # confirm
    ])
    out = io.StringIO()
    original = os.getcwd()
    try:
        os.chdir(tmp_path)
        rc = run_guided(force=False, inp=inp, out=out)
        assert rc == 0
        os.remove(tmp_path / ".local81" / "config.ini")
        from local81.config import load_config
        cfg = load_config(tmp_path / ".local81" / "config.ini")
    finally:
        os.chdir(original)
    assert cfg.project == "yamltest"
    assert cfg.scopes[0].servers == ["svr1", "svr2"]


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_guided_flag():
    from local81.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["init", "--guided"])
    assert args.guided is True
    assert args.command == "init"


def test_cli_init_without_guided():
    from local81.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["init"])
    assert args.guided is False


def test_preview_shown_before_write():
    inp = _make_input([
        "prev",          # project
        "main",          # scope
        "/src",          # source
        "y",             # keep placeholder source
        "/dst",          # target
        "y",             # remote mkdir
        "host1",         # servers
        "",              # rsync opts
        "y",             # fail fast
        "y",             # backup
        "",              # suffix
        "",              # parallel
        "n",             # log hosts
    ])
    out = io.StringIO()
    answers = run_guided_interview(inp=inp, out=out)
    assert answers is not None
    config_text = generate_config(answers)
    preview_out = io.StringIO()
    preview_config(config_text, out=preview_out)
    preview = preview_out.getvalue()
    assert "[local81]" in preview
    assert '[scope "main"]' in preview
    assert "Config preview" in preview
    assert "YAML mirror" in preview
