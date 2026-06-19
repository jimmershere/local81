from __future__ import annotations

from pathlib import Path

from local81 import connectors
from local81.config import load_config, validate_config
from local81.connectors import SshConnector
from local81.ssh import SshOptions


# ---------------------------------------------------------------------------
# SshOptions rendering (pure)
# ---------------------------------------------------------------------------

def test_empty_options_render_nothing() -> None:
    opts = SshOptions()
    assert opts.ssh_args() == []
    assert opts.rsync_transport() == []
    assert opts.host_for("web1") == "web1"


def test_from_config_renders_expected_flags() -> None:
    opts = SshOptions.from_config({
        "user": "deploy",
        "port": "2222",
        "identity_file": "~/.ssh/floor2_key",
        "known_hosts": "~/.ssh/known_hosts",
        "strict_host_key_checking": "accept-new",
        "connect_timeout": "5",
    })
    args = opts.ssh_args()
    assert args == [
        "-i", "~/.ssh/floor2_key", "-o", "IdentitiesOnly=yes",
        "-p", "2222",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "UserKnownHostsFile=~/.ssh/known_hosts",
        "-o", "ConnectTimeout=5",
    ]
    transport = opts.rsync_transport()
    assert transport[0] == "-e"
    assert transport[1].startswith("ssh -i ")
    assert "StrictHostKeyChecking=accept-new" in transport[1]
    assert opts.host_for("web1") == "deploy@web1"
    # An explicit user on the host wins over the configured default.
    assert opts.host_for("root@web1") == "root@web1"


# ---------------------------------------------------------------------------
# SshConnector wiring (capture argv without executing)
# ---------------------------------------------------------------------------

def test_connector_applies_ssh_options(monkeypatch) -> None:
    captured: dict = {}

    def fake_run_remote(host, remote, *, ssh_bin="ssh", ssh_args=None, timeout_seconds=None, **kw):
        captured["remote"] = {"host": host, "ssh_args": ssh_args, "remote": remote}
        return object()

    def fake_run_local(argv, **kw):
        captured["local"] = argv
        return object()

    monkeypatch.setattr(connectors, "run_remote", fake_run_remote)
    monkeypatch.setattr(connectors, "run_local", fake_run_local)

    opts = SshOptions(user="deploy", port=2222, identity_file="/k")
    conn = SshConnector("web1", ssh_opts=opts)

    conn.run("uptime")
    assert captured["remote"]["host"] == "deploy@web1"
    assert "-i" in captured["remote"]["ssh_args"] and "/k" in captured["remote"]["ssh_args"]
    assert "-p" in captured["remote"]["ssh_args"]

    conn.put("/local/f", "/remote/f")
    argv = captured["local"]
    assert "-e" in argv  # rsync gets the ssh transport string
    assert "deploy@web1:/remote/f" in argv


def test_connector_default_is_unchanged(monkeypatch) -> None:
    """No [ssh] options => plain ssh host / rsync (backward compatible)."""
    captured: dict = {}

    def fake_run_remote(host, remote, *, ssh_bin="ssh", ssh_args=None, timeout_seconds=None, **kw):
        captured["remote"] = {"host": host, "ssh_args": ssh_args}
        return object()

    def fake_run_local(argv, **kw):
        captured["local"] = argv
        return object()

    monkeypatch.setattr(connectors, "run_remote", fake_run_remote)
    monkeypatch.setattr(connectors, "run_local", fake_run_local)

    conn = SshConnector("web1")
    conn.run("uptime")
    assert captured["remote"] == {"host": "web1", "ssh_args": []}

    conn.put("/local/f", "/remote/f")
    assert "-e" not in captured["local"]
    assert "web1:/remote/f" in captured["local"]


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------

_MINIMAL = """[local81]
version = 0.1
project = test

[defaults]
rsync_opts = -az

[ssh]
user = deploy
port = 2222
identity_file = ~/.ssh/floor2_key
strict_host_key_checking = accept-new

[scope "web"]
enabled = true
source_dir = {src}
target_dir = /srv/app
servers = web1
"""


def test_load_config_exposes_ssh_options(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".local81").mkdir(parents=True)
    (tmp_path / ".local81" / "config.ini").write_text(_MINIMAL.format(src=tmp_path / "src"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    opts = cfg.ssh_options()
    assert opts.user == "deploy"
    assert opts.port == 2222
    assert opts.identity_file == "~/.ssh/floor2_key"
    assert opts.strict_host_key_checking == "accept-new"


def _ssh_findings(tmp_path: Path) -> list:
    monkeypatch_path = tmp_path / ".local81" / "config.ini"
    findings = validate_config(monkeypatch_path)
    return [f for f in findings if "ssh" in f.detail.lower() and f.level == "FAIL"]


def test_validate_config_accepts_valid_ssh(tmp_path: Path) -> None:
    (tmp_path / ".local81").mkdir(parents=True)
    (tmp_path / ".local81" / "config.ini").write_text(_MINIMAL.format(src=tmp_path / "src"), encoding="utf-8")
    # No [ssh]-specific FAIL findings for a valid section.
    assert _ssh_findings(tmp_path) == []


def test_validate_config_rejects_bad_ssh(tmp_path: Path) -> None:
    bad = _MINIMAL.replace("strict_host_key_checking = accept-new", "strict_host_key_checking = maybe")
    bad = bad.replace("port = 2222", "port = notaport")
    (tmp_path / ".local81").mkdir(parents=True)
    (tmp_path / ".local81" / "config.ini").write_text(bad.format(src=tmp_path / "src"), encoding="utf-8")
    details = " ".join(f.detail for f in _ssh_findings(tmp_path))
    assert "strict_host_key_checking" in details
    assert "port" in details
