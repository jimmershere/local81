from __future__ import annotations

import json
from pathlib import Path

import pytest

from local81 import become as become_mod
from local81.become import Become, resolve_become
from local81.commands import deploy
from local81.commands.deploy import run_deploy
from local81.secrets import SecretResolver
from local81.secrets.errors import SecretRefSyntaxError


# --- pure Become logic -----------------------------------------------------

def test_disabled_become_is_passthrough() -> None:
    b = Become()
    assert b.wrap("echo hi") == "echo hi"
    assert b.stdin is None


def test_enabled_become_wraps_with_sudo_S() -> None:
    b = Become(enabled=True, password="pw")
    wrapped = b.wrap("echo hi")
    assert wrapped.startswith("sudo -S -k -p ''")
    assert "bash -c 'echo hi'" in wrapped
    assert b.stdin == "pw\n"  # password fed on stdin, never in the command


def test_become_user_adds_dash_u() -> None:
    b = Become(enabled=True, user="deploy", password="pw")
    assert "-u deploy" in b.wrap("whoami")


def test_become_enabled_without_password_has_no_stdin() -> None:
    # NOPASSWD sudo: elevate but feed nothing on stdin.
    b = Become(enabled=True, password=None)
    assert b.wrap("x").startswith("sudo -S")
    assert b.stdin is None


# --- resolve_become --------------------------------------------------------

def test_resolve_become_disabled() -> None:
    r = SecretResolver(env={})
    assert resolve_become(r, password_ref=None, enabled=False) is become_mod.DISABLED


def test_resolve_become_resolves_ref_and_registers_for_scrub() -> None:
    r = SecretResolver(env={"SUDO_PW": "s3cret"})
    b = resolve_become(r, password_ref="env://SUDO_PW", enabled=True)
    assert b.enabled and b.password == "s3cret"
    assert r.scrub("pw is s3cret") == "pw is ***"


def test_resolve_become_rejects_literal_password() -> None:
    r = SecretResolver(env={})
    with pytest.raises(SecretRefSyntaxError):
        resolve_become(r, password_ref="not-a-ref", enabled=True)


# --- integration through run_deploy ----------------------------------------

def _write_become_plan(path: Path) -> None:
    payload = {
        "schema": "local81.plan.v0.1",
        "kind": "plan",
        "mode": "deploy",
        "plan_id": "p-become",
        "become": {"password_ref": "env://SUDO_PW"},
        "scopes": [{"scope": "web", "steps": [
            {"id": "scope:web:0001", "type": "rsync", "host": "local",
             "cmd": "id", "become": True},
        ]}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_run_deploy_sudo_wraps_and_scrubs(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_become_plan(plan_path)

    captured: dict = {}

    def fake_run_shell(command, timeout_seconds=None, *, dry_run=False, stdin_input=None):
        captured["command"] = command
        captured["stdin"] = stdin_input
        # Simulate a command that echoes the password into its output.
        return 0, "elevated; pw was s3cret", "", False

    monkeypatch.setattr(deploy, "_run_shell", fake_run_shell)

    resolver = SecretResolver(env={"SUDO_PW": "s3cret"})
    rc = run_deploy(plan=str(plan_path), scope="web", dry_run=False, resolver=resolver)
    assert rc == 0

    # The step ran under sudo -S, with the password delivered on stdin only.
    assert captured["command"].startswith("sudo -S -k -p ''")
    assert "bash -c id" in captured["command"]
    assert captured["stdin"] == "s3cret\n"
    assert "s3cret" not in captured["command"]

    # And the password never lands in the run artifact.
    raw = next((tmp_path / ".local81" / "runs").glob("*/run.json")).read_text(encoding="utf-8")
    assert "s3cret" not in raw
    run = json.loads(raw)
    assert run["steps"][0]["stdout"] == "elevated; pw was ***"
    assert run["steps"][0]["become"] is True
