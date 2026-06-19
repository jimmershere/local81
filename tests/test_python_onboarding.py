from __future__ import annotations

import json
from pathlib import Path

from local81 import onboarding
from local81.commands.keys import run_keys
from local81.commands.onboard import run_onboard
from local81.models import CommandResult
from local81.onboarding import copy_id_argv, ensure_key, key_paths, parse_host_list
from local81.ssh import SshOptions


# ---------------------------------------------------------------------------
# Host list parsing
# ---------------------------------------------------------------------------

def test_parse_host_list_csv_and_file_dedup(tmp_path: Path) -> None:
    hf = tmp_path / "hosts.txt"
    hf.write_text("# header\n10.0.0.1\tweb1\tweb1-alias\nweb2\n\nweb1\n", encoding="utf-8")
    out = parse_host_list("web1, web3 ,web1", str(hf))
    # order preserved, dupes removed; file's tab row contributes its first token
    assert out == ["web1", "web3", "10.0.0.1", "web2"]


# ---------------------------------------------------------------------------
# Key lifecycle (real ssh-keygen, into tmp)
# ---------------------------------------------------------------------------

def test_ensure_key_generates_and_is_idempotent(tmp_path: Path) -> None:
    priv, pub = key_paths(str(tmp_path / "id_ed25519"))
    s1 = ensure_key(priv, pub, execute=True)
    assert s1.exists and s1.created and priv.is_file() and pub.is_file()
    assert s1.private_mode == "0o600"
    assert s1.perms_ok
    first = priv.read_bytes()
    # Second ensure must not overwrite.
    s2 = ensure_key(priv, pub, execute=True)
    assert not s2.created
    assert priv.read_bytes() == first


def test_ensure_key_plan_does_not_create(tmp_path: Path) -> None:
    priv, pub = key_paths(str(tmp_path / "id_ed25519"))
    status = ensure_key(priv, pub, execute=False)
    assert not status.exists
    assert not priv.exists()


def test_copy_id_argv_reflects_ssh_options() -> None:
    opts = SshOptions(user="deploy", port=2222, strict_host_key_checking="accept-new")
    argv = copy_id_argv("web1", Path("/k.pub"), opts)
    assert argv[0] == "ssh-copy-id"
    assert "/k.pub" in argv
    assert "-p" in argv and "2222" in argv
    assert "StrictHostKeyChecking=accept-new" in " ".join(argv)
    assert argv[-1] == "deploy@web1"


# ---------------------------------------------------------------------------
# probe_host: simulate ssh via monkeypatched run_local
# ---------------------------------------------------------------------------

def _fake_run_local_factory(rc: int, stdout: str = "", stderr: str = ""):
    calls: list[list[str]] = []

    def fake(argv, **kwargs):
        calls.append(argv)
        return CommandResult(command=argv, returncode=rc, stdout=stdout, stderr=stderr)

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


def test_probe_host_ready(monkeypatch) -> None:
    stdout = "UP= 10:00:00 up 1 day\nPY=Python 3.12.3\nRS=rsync  version 3.2.7\nSH=yes\n"
    monkeypatch.setattr(onboarding, "run_local", _fake_run_local_factory(0, stdout))
    monkeypatch.setattr(onboarding, "tcp_open", lambda *a, **k: True)
    probe = onboarding.probe_host("web1", SshOptions(user="deploy"))
    assert probe.key_authed and probe.ready
    assert probe.python3 == "Python 3.12.3"
    assert probe.sha256sum is True
    assert probe.target == "deploy@web1"


def test_probe_host_reachable_not_authed(monkeypatch) -> None:
    monkeypatch.setattr(onboarding, "run_local", _fake_run_local_factory(255, "", "Permission denied (publickey)."))
    monkeypatch.setattr(onboarding, "tcp_open", lambda *a, **k: True)
    probe = onboarding.probe_host("web1")
    assert probe.tcp_open and not probe.key_authed and not probe.ready
    assert "Permission denied" in probe.error


# ---------------------------------------------------------------------------
# keys command
# ---------------------------------------------------------------------------

def test_keys_copy_plan_does_not_execute(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    priv, pub = key_paths(str(tmp_path / "id_ed25519"))
    ensure_key(priv, pub, execute=True)
    # Guard: run_local must never be called for ssh-copy-id in plan mode.
    monkeypatch.setattr(onboarding, "run_local", _fake_run_local_factory(0))
    fake = onboarding.run_local
    rc = run_keys("copy", path=str(priv), hosts="web1,web2", execute=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("[plan]") == 2
    assert "ssh-copy-id" in out
    assert fake.calls == []  # type: ignore[attr-defined]


def test_keys_copy_execute_invokes_ssh_copy_id(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    priv, pub = key_paths(str(tmp_path / "id_ed25519"))
    ensure_key(priv, pub, execute=True)
    fake = _fake_run_local_factory(0)
    monkeypatch.setattr(onboarding, "run_local", fake)
    rc = run_keys("copy", path=str(priv), hosts="web1", execute=True)
    assert rc == 0
    assert any(c[0] == "ssh-copy-id" for c in fake.calls)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# onboard command
# ---------------------------------------------------------------------------

def test_onboard_plan_writes_report_and_no_mutation(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    # All hosts reachable but not key-authed.
    monkeypatch.setattr(onboarding, "run_local", _fake_run_local_factory(255, "", "Permission denied (publickey)."))
    monkeypatch.setattr(onboarding, "tcp_open", lambda *a, **k: True)

    rc = run_onboard(hosts="web1,web2", path=str(tmp_path / "id_ed25519"), execute=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "would generate" in out  # key not created in plan mode
    assert not (tmp_path / "id_ed25519").exists()
    reports = list((tmp_path / ".local81" / "reports").glob("onboard-*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["schema"] == "local81.onboard.v0.1"
    assert payload["execute"] is False
    assert {h["host"] for h in payload["hosts"]} == {"web1", "web2"}
    # plan mode records intended ssh-copy-id but did not execute it
    assert all(c["executed"] is False for c in payload["copies"])


# ---------------------------------------------------------------------------
# doctor --fleet
# ---------------------------------------------------------------------------

_FLEET_CONFIG = """[local81]
version = 0.1
project = test

[defaults]
rsync_opts = -az

[ssh]
user = deploy

[scope "web"]
enabled = true
source_dir = {src}
target_dir = /srv/app
servers = web1,web2
"""


def test_doctor_fleet_probes_configured_hosts(tmp_path: Path, monkeypatch, capsys) -> None:
    from local81.commands import doctor

    (tmp_path / ".local81").mkdir(parents=True)
    (tmp_path / ".local81" / "config.ini").write_text(_FLEET_CONFIG.format(src=tmp_path / "src"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def fake_probe(host, ssh_opts=None, *, connect_timeout=5):
        p = onboarding.HostProbe(host=host, target=f"deploy@{host}")
        if host == "web1":
            p.tcp_open = p.key_authed = p.sha256sum = True
            p.python3, p.rsync = "Python 3.12.3", "rsync 3.2.7"
        else:
            p.tcp_open = True  # reachable, not authed
        return p

    monkeypatch.setattr(onboarding, "probe_host", fake_probe)
    # Probe classification is the unit under test; run_doctor's rc is dominated
    # by unrelated schema findings from this minimal config.
    checks = doctor._fleet_checks(None)
    by_host = {c.name: c for c in checks}
    assert by_host["fleet:web1"].level == "PASS" and "ready" in by_host["fleet:web1"].detail
    assert by_host["fleet:web2"].level == "WARN" and "not key-authed" in by_host["fleet:web2"].detail


# ---------------------------------------------------------------------------
# CLI wiring smoke tests
# ---------------------------------------------------------------------------

def test_cli_parses_keys_and_onboard_and_fleet() -> None:
    from local81.cli import build_parser

    parser = build_parser()
    a = parser.parse_args(["keys", "copy", "--hosts", "web1,web2", "--execute"])
    assert a.command == "keys" and a.keys_command == "copy" and a.execute is True

    b = parser.parse_args(["onboard", "--hosts", "web1", "--hosts-file", "h.txt"])
    assert b.command == "onboard" and b.hosts == "web1" and b.hosts_file == "h.txt"

    c = parser.parse_args(["doctor", "--fleet"])
    assert c.command == "doctor" and c.fleet is True
