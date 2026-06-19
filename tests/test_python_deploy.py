from __future__ import annotations

import hashlib
import json
from pathlib import Path

from local81.commands.deploy import _resolve_plan_path, parse_hosts_file, run_check, run_deploy


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_v2_file_plan(path: Path, *, target: Path, sha256: str, cmd: str,
                        scope_name: str = "web") -> None:
    """A v2 desired-state plan with a single local file.synced op-step."""
    step = {
        "id": f"scope:{scope_name}:0001",
        "type": "rsync",
        "host": "@local",
        "cmd": cmd,
        "op": "file.synced",
        "intent": {"path": str(target), "sha256": sha256},
        "remote_path": str(target),
    }
    payload = {
        "schema": "local81.plan.v2",
        "kind": "plan",
        "mode": "deploy",
        "plan_id": "v2p1",
        "scopes": [{"scope": scope_name, "steps": [step]}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_plan(path: Path, *, cmd: str = 'printf ok', rollback: bool = False,
                host: str = "web1", scope_name: str = "web",
                extra: dict | None = None) -> None:
    step = {
        "id": f"scope:{scope_name}:0001",
        "type": "rsync",
        "host": host,
        "cmd": cmd,
    }
    if rollback:
        step["rollback"] = {"cmd": "printf rollback"}
    payload = {
        "schema": "local81.plan.v0.1",
        "kind": "plan",
        "mode": "deploy",
        "plan_id": "p1",
        "scopes": [{"scope": scope_name, "steps": [step]}],
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_multi_host_plan(path: Path, hosts: list[str]) -> None:
    steps = []
    for i, host in enumerate(hosts, 1):
        steps.append({
            "id": f"scope:web:{i:04d}",
            "type": "rsync",
            "host": host,
            "cmd": "printf ok",
        })
    payload = {
        "schema": "local81.plan.v0.1",
        "kind": "plan",
        "mode": "deploy",
        "plan_id": "multi1",
        "scopes": [{"scope": "web", "steps": steps}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_hosts_file(path: Path, hosts: list[tuple[str, str, str]]) -> None:
    lines = ["# host\tserver\talias\toptional_flag"]
    for ip, server, alias in hosts:
        lines.append(f"{ip}\t{server}\t{alias}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Existing tests (preserved)
# ---------------------------------------------------------------------------

def test_resolve_latest_plan_path(tmp_path: Path) -> None:
    plans_dir = tmp_path / ".local81" / "plans"
    plans_dir.mkdir(parents=True)
    older = plans_dir / "20260430T010101Z-aaaa1111.plan.json"
    newer = plans_dir / "20260501T020202Z-bbbb2222.plan.json"
    older.write_text("{}", encoding="utf-8")
    newer.write_text("{}", encoding="utf-8")

    assert _resolve_plan_path(use_latest=True, plans_dir=str(plans_dir)) == newer


def test_run_deploy_dry_run_writes_run_record_but_not_state(tmp_path: Path, monkeypatch, capsys) -> None:
    """A dry run records a run, but must be side-effect free w.r.t. scope state.

    Regression guard for the dry-run state-poisoning defect: a --dry-run deploy
    used to call _update_scope_state, stamping last_success=now. With the
    mtime_since_last_success discovery strategy that made the *next real deploy*
    select zero files and ship nothing. A dry run must never write scope state.
    """
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path)

    rc = run_deploy(plan=str(plan_path), scope="web", dry_run=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert "dry run" in out
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    assert len(run_files) == 1
    run = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert run["dry_run"] is True
    assert run["steps"][0]["stdout"] == ""
    assert (run_files[0].parent / "run.log").exists()
    # The defining assertion: no scope state file is written by a dry run, so
    # last_success is never poisoned.
    assert not (tmp_path / ".local81" / "state" / "web.json").exists()


def test_run_record_persists_reversibility(tmp_path: Path, monkeypatch, capsys) -> None:
    """A step with a recorded rollback cmd is marked reversible in run.json.

    This is what makes after-the-fact `local81 rollback <run-id>` possible: the
    run manifest must carry the per-step rollback block, not just the on-failure
    replay path.
    """
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path, rollback=True)

    rc = run_deploy(plan=str(plan_path), scope="web", dry_run=True)
    capsys.readouterr()
    assert rc == 0
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    run = json.loads(run_files[0].read_text(encoding="utf-8"))
    step = run["steps"][0]
    assert step["reversible"] is True
    assert step["rollback"]["cmd"] == "printf rollback"


def test_run_record_marks_non_reversible_step(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path, rollback=False)

    rc = run_deploy(plan=str(plan_path), scope="web", dry_run=True)
    capsys.readouterr()
    assert rc == 0
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    run = json.loads(run_files[0].read_text(encoding="utf-8"))
    step = run["steps"][0]
    assert step["reversible"] is False
    assert "rollback" not in step


def test_dry_run_then_real_deploy_still_ships_files(tmp_path: Path, monkeypatch, capsys) -> None:
    """End-to-end regression: dry-run a scope, then really deploy it.

    The real deploy must still run its steps and stamp last_success — proving the
    preceding dry run did not mark the scope as already-deployed.
    """
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path)

    # 1) Preview with a dry run. Must not write state.
    assert run_deploy(plan=str(plan_path), scope="web", dry_run=True) == 0
    capsys.readouterr()
    assert not (tmp_path / ".local81" / "state" / "web.json").exists()

    # 2) Now deploy for real. State is written with a real last_success.
    rc = run_deploy(plan=str(plan_path), scope="web", dry_run=False)
    capsys.readouterr()
    assert rc == 0
    state_path = tmp_path / ".local81" / "state" / "web.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_plan_id"] == "p1"
    assert state["files_last_deployed_count"] == 1
    assert state["last_success"]  # non-empty timestamp


def test_failed_real_deploy_does_not_stamp_last_success(tmp_path: Path, monkeypatch, capsys) -> None:
    """A real deploy that fails must not stamp last_success (no state file)."""
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path, cmd='printf broken >&2; exit 3')

    rc = run_deploy(plan=str(plan_path), scope="web", dry_run=False, fail_fast=True)
    capsys.readouterr()

    assert rc == 3
    assert not (tmp_path / ".local81" / "state" / "web.json").exists()


def test_run_deploy_failure_records_stderr(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path, cmd='printf broken >&2; exit 7')

    rc = run_deploy(plan=str(plan_path), scope="web", dry_run=False, fail_fast=True)
    out = capsys.readouterr().out

    assert rc == 7
    assert "broken" in out
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    run = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert run["rc"] == 7
    assert run["steps"][0]["stderr"] == "broken"
    assert not (tmp_path / ".local81" / "state" / "web.json").exists()


def test_run_deploy_missing_scope_is_friendly(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path)

    rc = run_deploy(plan=str(plan_path), scope="api")
    out = capsys.readouterr().out

    assert rc == 1
    assert "did not find any matching scopes" in out


# ---------------------------------------------------------------------------
# v2 desired-state: deploy-time convergence gate (live run, no network)
# ---------------------------------------------------------------------------

def test_run_deploy_skips_converged_op_step(tmp_path: Path, monkeypatch, capsys) -> None:
    """A v2 file.synced step whose target already matches must not run its cmd.

    The deploy-time gate probes the live file, sees the sha256 already matches
    the intent, and records the step as converged (action=none) without ever
    executing the placement command.
    """
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "deployed.conf"
    desired = "payload v1\n"
    target.write_text(desired, encoding="utf-8")
    sentinel = tmp_path / "ran.flag"
    plan_path = tmp_path / "plan.json"
    # cmd would touch a sentinel if it ran; convergence must prevent that.
    _write_v2_file_plan(plan_path, target=target, sha256=_sha256(desired),
                        cmd=f"printf x > {sentinel}")

    rc = run_deploy(plan=str(plan_path), scope="web", dry_run=False)
    capsys.readouterr()

    assert rc == 0
    assert not sentinel.exists(), "converged step must not execute its command"
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    run = json.loads(run_files[0].read_text(encoding="utf-8"))
    step_rec = run["steps"][0]
    assert step_rec["action"] == "none"
    assert step_rec["converged"] is True
    assert step_rec["rc"] == 0
    # A converged file.synced step ships nothing, so the deployed-files count is 0.
    state = json.loads((tmp_path / ".local81" / "state" / "web.json").read_text(encoding="utf-8"))
    assert state["files_last_deployed_count"] == 0


def test_run_deploy_runs_drifted_op_step(tmp_path: Path, monkeypatch, capsys) -> None:
    """A v2 file.synced step whose target drifted must run its placement cmd."""
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "deployed.conf"
    target.write_text("stale bytes\n", encoding="utf-8")
    desired = "payload v2\n"
    plan_path = tmp_path / "plan.json"
    # The cmd converges the target to the desired bytes.
    _write_v2_file_plan(plan_path, target=target, sha256=_sha256(desired),
                        cmd=f"printf 'payload v2\\n' > {target}")

    rc = run_deploy(plan=str(plan_path), scope="web", dry_run=False)
    capsys.readouterr()

    assert rc == 0
    assert target.read_text(encoding="utf-8") == desired
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    run = json.loads(run_files[0].read_text(encoding="utf-8"))
    step_rec = run["steps"][0]
    assert step_rec["action"] == "update"
    assert not step_rec.get("converged")
    state = json.loads((tmp_path / ".local81" / "state" / "web.json").read_text(encoding="utf-8"))
    assert state["files_last_deployed_count"] == 1


# ---------------------------------------------------------------------------
# v2 desired-state: deploy --check drift guard
# ---------------------------------------------------------------------------

def test_check_fails_on_target_drift(tmp_path: Path, monkeypatch, capsys) -> None:
    """--check re-gathers facts and fails when a target diverges from intent."""
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "deployed.conf"
    target.write_text("hand-edited drift\n", encoding="utf-8")  # differs from desired
    plan_path = tmp_path / "plan.json"
    _write_v2_file_plan(plan_path, target=target, sha256=_sha256("payload v1\n"),
                        cmd=f"printf x > {target}")

    rc = run_check(plan=str(plan_path))
    out = capsys.readouterr().out

    assert rc == 1
    assert "Desired-state drift:" in out
    assert "drift=1" in out
    assert "target drift" in out
    assert "--allow-drift" in out


def test_check_allow_drift_downgrades_to_warning(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "deployed.conf"
    target.write_text("hand-edited drift\n", encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    _write_v2_file_plan(plan_path, target=target, sha256=_sha256("payload v1\n"),
                        cmd=f"printf x > {target}")

    rc = run_check(plan=str(plan_path), allow_drift=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert "allowed by --allow-drift" in out


def test_check_converged_target_is_not_drift(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "deployed.conf"
    desired = "payload v1\n"
    target.write_text(desired, encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    _write_v2_file_plan(plan_path, target=target, sha256=_sha256(desired),
                        cmd=f"printf x > {target}")

    rc = run_check(plan=str(plan_path))
    out = capsys.readouterr().out

    assert rc == 0
    assert "converged=1" in out
    assert "drift=0" in out


def test_check_absent_target_is_create_not_drift(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "not-there-yet.conf"
    plan_path = tmp_path / "plan.json"
    _write_v2_file_plan(plan_path, target=target, sha256=_sha256("payload v1\n"),
                        cmd=f"printf x > {target}")

    rc = run_check(plan=str(plan_path))
    out = capsys.readouterr().out

    assert rc == 0
    assert "create=1" in out
    assert "drift=0" in out


# ---------------------------------------------------------------------------
# Phase 2: --check mode
# ---------------------------------------------------------------------------

def test_check_valid_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path)

    rc = run_check(plan=str(plan_path))
    out = capsys.readouterr().out

    assert rc == 0
    assert "Check passed" in out
    assert "Scopes: 1" in out
    assert "Total steps: 1" in out


def test_check_warns_for_missing_config_fingerprint(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path)

    rc = run_check(plan=str(plan_path))
    out = capsys.readouterr().out

    assert rc == 0
    assert "[warn] plan is missing config_fingerprint provenance metadata" in out


def test_check_warns_for_stale_config_fingerprint(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / ".local81"
    config_dir.mkdir()
    (config_dir / "config.ini").write_text(
        "[local81]\n"
        "version = 0.1\n"
        "project = test\n"
        "default_scope = web\n"
        "state_dir = .local81/state\n"
        "plans_dir = .local81/plans\n"
        "runs_dir = .local81/runs\n"
        "logs_dir = .local81/logs\n"
        "lock_file = .local81/local81.lock\n"
        "require_plan_for_deploy = true\n"
        "fail_fast = true\n"
        "max_parallel = 1\n"
        "shell = /usr/bin/bash\n"
        "\n"
        "[tools]\n"
        "ssh = /usr/bin/ssh\n"
        "rsync = /usr/bin/rsync\n"
        "find = /usr/bin/find\n"
        "\n"
        "[defaults]\n"
        "rsync_opts = -az\n"
        "backup = false\n"
        "backup_suffix = .bkp\n"
        "remote_mkdir = true\n"
        "dry_run_default = false\n"
        "log_hosts =\n"
        "log_dest_dir = .local81/pulled-logs\n"
        "jboss_log_path =\n"
        "apache_log_path =\n"
        "engin_log_path =\n"
        "smartxfr_log_path =\n"
        "\n"
        "[routing]\n"
        "env_from_filename_prefix = s:sys,q:qa,p:production\n"
        "env_from_server_name_char_at = 4\n"
        "env_from_server_name_char_map = s:sys,q:qa,p:production\n"
        "\n"
        "[access]\n"
        "allowed_users =\n"
        "allowed_groups =\n"
        "denied_users =\n"
        "deny_root = false\n"
        "allow_remote_cmd = false\n"
        "\n"
        "[scope \"web\"]\n"
        "enabled = true\n"
        "source_dir = /tmp/source\n"
        "target_dir = /srv/target\n"
        "servers = web1\n"
        "discovery = mtime_since_last_success\n",
        encoding="utf-8",
    )
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path, extra={
        "local81_version": "0.1",
        "created_at": "2026-01-01T00:00:00Z",
        "config_fingerprint": "sha256:" + ("0" * 64),
    })

    rc = run_check(plan=str(plan_path))
    out = capsys.readouterr().out

    assert rc == 0
    assert "[warn] plan config_fingerprint does not match current config .local81/config.ini" in out


def test_check_latest_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plans_dir = tmp_path / ".local81" / "plans"
    plans_dir.mkdir(parents=True)
    plan_path = plans_dir / "20260501T020202Z-p1.plan.json"
    _write_plan(plan_path)

    rc = run_check(use_latest=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert ".local81/plans/20260501T020202Z-p1.plan.json" in out


def test_check_missing_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    rc = run_check(plan=str(tmp_path / "nope.json"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "could not find" in out


def test_check_invalid_json(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    rc = run_check(plan=str(bad))
    out = capsys.readouterr().out
    assert rc == 1
    assert "not valid JSON" in out


def test_check_missing_keys(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "partial.json"
    p.write_text(json.dumps({"kind": "plan"}), encoding="utf-8")
    rc = run_check(plan=str(p))
    out = capsys.readouterr().out
    assert rc == 1
    assert "missing required key" in out


def test_check_wrong_kind(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "wrong.json"
    p.write_text(json.dumps({
        "kind": "report", "mode": "deploy", "schema": "local81.plan.v0.1",
        "plan_id": "x", "scopes": [],
    }), encoding="utf-8")
    rc = run_check(plan=str(p))
    out = capsys.readouterr().out
    assert rc == 1
    assert "kind should be 'plan'" in out


def test_check_via_run_deploy(tmp_path: Path, monkeypatch, capsys) -> None:
    """--check flag passed through run_deploy."""
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path)
    rc = run_deploy(plan=str(plan_path), check=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Check passed" in out


# ---------------------------------------------------------------------------
# Phase 2: hosts file parsing
# ---------------------------------------------------------------------------

def test_parse_hosts_file(tmp_path: Path) -> None:
    hf = tmp_path / "hosts.txt"
    _write_hosts_file(hf, [
        ("10.0.0.1", "server1", "s1"),
        ("10.0.0.2", "server2", "s2"),
    ])
    hosts = parse_hosts_file(str(hf))
    assert len(hosts) == 2
    assert hosts[0] == {"ip": "10.0.0.1", "server": "server1", "alias": "s1"}
    assert hosts[1] == {"ip": "10.0.0.2", "server": "server2", "alias": "s2"}


def test_parse_hosts_file_skips_comments(tmp_path: Path) -> None:
    hf = tmp_path / "hosts.txt"
    hf.write_text("# header\n10.0.0.1\tsvr1\ta1\n; comment\n10.0.0.2\tsvr2\ta2\n", encoding="utf-8")
    hosts = parse_hosts_file(str(hf))
    assert len(hosts) == 2


def test_parse_hosts_file_empty(tmp_path: Path) -> None:
    hf = tmp_path / "hosts.txt"
    hf.write_text("# only comments\n", encoding="utf-8")
    hosts = parse_hosts_file(str(hf))
    assert hosts == []


# ---------------------------------------------------------------------------
# Phase 2: integration tests (mocked, no network)
# ---------------------------------------------------------------------------

def test_deploy_dry_run_creates_run_record(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path)
    rc = run_deploy(plan=str(plan_path), dry_run=True)
    assert rc == 0
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    assert len(run_files) == 1
    data = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert data["dry_run"] is True
    assert data["rc"] == 0


def test_deploy_latest_plan_file(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plans_dir = tmp_path / ".local81" / "plans"
    plans_dir.mkdir(parents=True)
    older = plans_dir / "20260430T010101Z-old.plan.json"
    newer = plans_dir / "20260501T020202Z-new.plan.json"
    _write_plan(older, scope_name="old")
    _write_plan(newer, scope_name="web")

    rc = run_deploy(use_latest=True, dry_run=True)
    out = capsys.readouterr().out

    assert rc == 0
    assert ".local81/plans/20260501T020202Z-new.plan.json" in out


def test_deploy_missing_plan_file(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    rc = run_deploy(plan=str(tmp_path / "missing.json"))
    out = capsys.readouterr().out
    assert rc == 1
    assert "could not find" in out


def test_deploy_empty_hosts_file(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_plan(plan_path)
    hf = tmp_path / "empty_hosts.txt"
    hf.write_text("# nothing\n", encoding="utf-8")
    rc = run_deploy(plan=str(plan_path), hosts_file=str(hf))
    out = capsys.readouterr().out
    assert rc == 1
    assert "no hosts" in out


# ---------------------------------------------------------------------------
# Phase 3: multi-host deploy
# ---------------------------------------------------------------------------

def test_deploy_multi_host_sequential(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_multi_host_plan(plan_path, ["host1", "host2"])
    hf = tmp_path / "hosts.txt"
    _write_hosts_file(hf, [("10.0.0.1", "host1", "host1"), ("10.0.0.2", "host2", "host2")])

    rc = run_deploy(plan=str(plan_path), dry_run=True, hosts_file=str(hf))
    out = capsys.readouterr().out

    assert rc == 0
    assert "Per-host results:" in out
    assert "host1: ok" in out
    assert "host2: ok" in out
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    data = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert "hosts" in data
    assert len(data["hosts"]) == 2


def test_deploy_multi_host_parallel(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_multi_host_plan(plan_path, ["hostA", "hostB", "hostC"])
    hf = tmp_path / "hosts.txt"
    _write_hosts_file(hf, [
        ("10.0.0.1", "hostA", "hostA"),
        ("10.0.0.2", "hostB", "hostB"),
        ("10.0.0.3", "hostC", "hostC"),
    ])

    rc = run_deploy(plan=str(plan_path), dry_run=True, hosts_file=str(hf), parallel=True, max_parallel=3)
    out = capsys.readouterr().out

    assert rc == 0
    assert "Parallel: yes" in out
    assert "Per-host results:" in out


def test_deploy_remote_cmd_uses_ssh_target(tmp_path: Path, monkeypatch, capsys) -> None:
    import local81.commands.deploy as deploy_module

    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    payload = {
        "schema": "local81.plan.v0.1",
        "kind": "plan",
        "mode": "deploy",
        "plan_id": "remote1",
        "scopes": [{"scope": "web", "steps": [
            {"id": "scope:web:0001", "type": "remote_cmd", "server": "web1", "cmd": "systemctl status app"}
        ]}],
    }
    plan_path.write_text(json.dumps(payload), encoding="utf-8")

    captured: list[tuple[str, str]] = []

    def fake_run_remote(host: str, command: str, timeout_seconds=None, *, dry_run: bool = False, stdin_input=None):
        captured.append((host, command))
        return 0, "ok", "", False

    monkeypatch.setattr(deploy_module, "_run_remote", fake_run_remote)

    rc = run_deploy(plan=str(plan_path), dry_run=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "on web1" in out
    assert captured == [("web1", "systemctl status app")]
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    run = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert run["steps"][0]["host"] == "web1"


def test_deploy_multi_host_fail_fast(tmp_path: Path, monkeypatch, capsys) -> None:
    """With fail-fast, a failing host should prevent subsequent hosts in sequential mode."""
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    # First host will fail, second should not run (sequential + fail-fast)
    steps = [
        {"id": "scope:web:0001", "type": "rsync", "host": "badhost",
         "cmd": "exit 1"},
        {"id": "scope:web:0002", "type": "rsync", "host": "goodhost",
         "cmd": "printf ok"},
    ]
    payload = {
        "schema": "local81.plan.v0.1", "kind": "plan", "mode": "deploy",
        "plan_id": "ff1",
        "scopes": [{"scope": "web", "steps": steps}],
    }
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    hf = tmp_path / "hosts.txt"
    _write_hosts_file(hf, [("10.0.0.1", "badhost", "badhost"), ("10.0.0.2", "goodhost", "goodhost")])

    rc = run_deploy(plan=str(plan_path), hosts_file=str(hf), fail_fast=True)
    out = capsys.readouterr().out

    assert rc != 0
    assert "FAILED" in out


def test_deploy_per_host_status_output(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_multi_host_plan(plan_path, ["alpha", "beta"])
    hf = tmp_path / "hosts.txt"
    _write_hosts_file(hf, [("10.0.0.1", "alpha", "alpha"), ("10.0.0.2", "beta", "beta")])

    rc = run_deploy(plan=str(plan_path), dry_run=True, hosts_file=str(hf))
    out = capsys.readouterr().out

    assert rc == 0
    assert "alpha" in out
    assert "beta" in out
    assert "Per-host results:" in out


# ---------------------------------------------------------------------------
# CLI parser tests
# ---------------------------------------------------------------------------

def test_cli_deploy_latest_flag() -> None:
    from local81.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["deploy", "--latest"])
    assert args.latest is True
    assert args.plan is None


def test_cli_deploy_check_flag() -> None:
    from local81.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["deploy", "--plan", "x.json", "--check"])
    assert args.check is True


def test_cli_deploy_hosts_file_flag() -> None:
    from local81.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["deploy", "--plan", "x.json", "--hosts-file", "hosts.txt"])
    assert args.hosts_file == "hosts.txt"


def test_cli_deploy_parallel_flag() -> None:
    from local81.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["deploy", "--plan", "x.json", "--parallel"])
    assert args.parallel is True


def test_cli_deploy_allow_drift_flag() -> None:
    from local81.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["deploy", "--plan", "x.json", "--check", "--allow-drift"])
    assert args.allow_drift is True
    default = parser.parse_args(["deploy", "--plan", "x.json", "--check"])
    assert default.allow_drift is False


def test_cli_history_command() -> None:
    from local81.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["history"])
    assert args.command == "history"
    assert args.limit == 20


def test_cli_logs_command() -> None:
    from local81.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["logs", "run-123"])
    assert args.command == "logs"
    assert args.run_id == "run-123"


def test_cli_diff_command() -> None:
    from local81.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["diff", "a.json", "b.json"])
    assert args.command == "diff"
    assert args.plan_a == "a.json"
    assert args.plan_b == "b.json"


# ---------------------------------------------------------------------------
# Phase 4: fleet execution (forks / serial / max-fail / limit)
# ---------------------------------------------------------------------------

def _write_fleet_plan(path: Path, hosts: list[str], *, fail_hosts: set[str] | None = None,
                      scope_name: str = "web") -> None:
    fail_hosts = fail_hosts or set()
    steps = []
    for i, host in enumerate(hosts, 1):
        steps.append({
            "id": f"scope:{scope_name}:{i:04d}",
            "type": "rsync",
            "host": host,
            "cmd": "exit 1" if host in fail_hosts else "printf ok",
        })
    payload = {
        "schema": "local81.plan.v0.1", "kind": "plan", "mode": "deploy",
        "plan_id": "fleet1",
        "scopes": [{"scope": scope_name, "steps": steps}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_fleet_deploy_runs_all_hosts_with_forks(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_fleet_plan(plan_path, ["h1", "h2", "h3"])

    rc = run_deploy(plan=str(plan_path), forks=3)
    out = capsys.readouterr().out

    assert rc == 0
    assert "Fleet summary:" in out
    assert "totals: ok=3" in out
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    data = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert {h["host"] for h in data["hosts"]} == {"h1", "h2", "h3"}
    assert all(h["status"] == "changed" for h in data["hosts"])
    # Per-host log files are written for each fleet host.
    for host in ("h1", "h2", "h3"):
        assert (run_files[0].parent / f"{host}.log").is_file()


def test_fleet_chaos_serial_maxfail_stops_before_next_batch(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    hosts = [f"h{i}" for i in range(10)]
    # h1 is in the first batch (h0,h1,h2) and rigged to fail.
    _write_fleet_plan(plan_path, hosts, fail_hosts={"h1"})

    rc = run_deploy(plan=str(plan_path), forks=3, serial="3", max_fail="1")
    out = capsys.readouterr().out

    assert rc != 0
    assert "aborted" in out
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    data = json.loads(run_files[0].read_text(encoding="utf-8"))
    by_host = {h["host"]: h["status"] for h in data["hosts"]}
    assert by_host["h1"] == "failed"
    assert by_host["h0"] == "changed"
    assert by_host["h2"] == "changed"
    for i in range(3, 10):
        assert by_host[f"h{i}"] == "skipped"


def test_fleet_limit_filters_hosts(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_fleet_plan(plan_path, ["web1", "web2", "db1"])

    rc = run_deploy(plan=str(plan_path), limit="web*")
    capsys.readouterr()

    assert rc == 0
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    data = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert {h["host"] for h in data["hosts"]} == {"web1", "web2"}


def test_logs_host_renders_per_host_log(tmp_path: Path, monkeypatch, capsys) -> None:
    from local81.commands.logs import run_logs

    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_fleet_plan(plan_path, ["h1", "h2"])
    run_deploy(plan=str(plan_path), forks=2)
    run_id = list((tmp_path / ".local81" / "runs").glob("*/"))[0].name
    capsys.readouterr()

    rc = run_logs(run_id, host="h1")
    out = capsys.readouterr().out
    assert rc == 0
    assert "host=h1" in out


def test_cli_deploy_fleet_flags() -> None:
    from local81.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["deploy", "--latest", "--forks", "10", "--serial", "3",
                              "--max-fail", "1", "--limit", "web*"])
    assert args.forks == 10
    assert args.serial == "3"
    assert args.max_fail == "1"
    assert args.limit == "web*"


def test_cli_logs_host_flag() -> None:
    from local81.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["logs", "run-1", "--host", "web1"])
    assert args.host == "web1"


def _write_secret_echo_plan(path: Path, *, secret: str) -> None:
    """A live (non-dry) plan whose step echoes a secret value to stdout."""
    payload = {
        "schema": "local81.plan.v0.1",
        "kind": "plan",
        "mode": "deploy",
        "plan_id": "p-secret",
        # type 'rsync' with no op/intent runs the raw cmd via the local shell.
        "scopes": [{"scope": "web", "steps": [
            {"id": "scope:web:0001", "type": "rsync", "host": "local",
             "cmd": f"printf '%s' {secret}"},
        ]}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_run_record_scrubs_resolved_secret_from_run_json(tmp_path: Path, monkeypatch, capsys) -> None:
    """A secret the resolver handed out must never land in run.json in cleartext.

    Backs the on-disk half of the "secrets never at rest" guarantee: even when a
    deployed command emits a secret to stdout, the per-run scrubber masks it.
    """
    from local81.secrets import SecretResolver

    secret = "topsecret123value"
    monkeypatch.setenv("LOCAL81_TEST_SECRET", secret)
    monkeypatch.chdir(tmp_path)

    resolver = SecretResolver(env={"LOCAL81_TEST_SECRET": secret})
    assert resolver.resolve("env://LOCAL81_TEST_SECRET") == secret  # now in _seen

    plan_path = tmp_path / "plan.json"
    _write_secret_echo_plan(plan_path, secret=secret)

    rc = run_deploy(plan=str(plan_path), scope="web", dry_run=False, resolver=resolver)
    assert rc == 0

    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    assert len(run_files) == 1
    raw = run_files[0].read_text(encoding="utf-8")
    # The command really did emit the secret (step captured stdout)...
    run = json.loads(raw)
    assert run["steps"][0]["stdout"] == "***"
    # ...and nowhere in the on-disk artifact does the cleartext survive.
    assert secret not in raw
    assert "***" in raw


def test_run_record_unchanged_when_no_secret_resolved(tmp_path: Path, monkeypatch, capsys) -> None:
    """No resolved secrets => scrub is an identity no-op (no false masking)."""
    monkeypatch.chdir(tmp_path)
    plan_path = tmp_path / "plan.json"
    _write_secret_echo_plan(plan_path, secret="plainoutput")

    rc = run_deploy(plan=str(plan_path), scope="web", dry_run=False)
    assert rc == 0
    run_files = list((tmp_path / ".local81" / "runs").glob("*/run.json"))
    run = json.loads(run_files[0].read_text(encoding="utf-8"))
    assert run["steps"][0]["stdout"] == "plainoutput"
