from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import monotonic

from local81.become import DISABLED as _BECOME_DISABLED
from local81.become import Become, resolve_become
from local81.config import load_config, resolve_config_path, validate_config
from local81.execution_safety import summarize_execution_risks
from local81.fleet import HostOutcome, classify, render_summary, run_fleet
from local81.hooks import run_hook
from local81.notifications import NotificationEvent, notify_all
from local81.plan_integrity import plan_provenance_warnings
from local81.policy import enforce_deploy_policy
from local81.resolve import resolve_step_action
from local81.secrets import SecretResolver
from local81.state import load_scope_state

# A scrubber masks resolved secret values out of any text before it lands on
# disk. ``SecretResolver.scrub`` is the real one; this no-op is the default so
# callers that never resolve a secret stay byte-for-byte unchanged.
def _no_scrub(text: str) -> str:
    return text

_print_lock = Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_plan(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_plan_path(*, plan: str | None = None, use_latest: bool = False, plans_dir: str = ".local81/plans") -> Path | None:
    if use_latest:
        candidates = sorted(Path(plans_dir).glob("*.plan.json"))
        return candidates[-1] if candidates else None
    if plan:
        return Path(plan)
    return None


def _run_shell(command: str, timeout_seconds: int | None = None, *, dry_run: bool = False,
               stdin_input: str | None = None) -> tuple[int, str, str, bool]:
    if dry_run:
        return 0, "", "", False
    try:
        proc = subprocess.run(["bash", "-lc", command], text=True, capture_output=True,
                              timeout=timeout_seconds, input=stdin_input)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip(), False
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
        return 124, stdout, stderr or "step timed out", True


def _run_remote(host: str, command: str, timeout_seconds: int | None = None, *, dry_run: bool = False,
                stdin_input: str | None = None) -> tuple[int, str, str, bool]:
    if dry_run:
        return 0, "", "", False
    try:
        proc = subprocess.run(["ssh", host, command], text=True, capture_output=True,
                              timeout=timeout_seconds, input=stdin_input)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip(), False
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "").strip() if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "").strip() if isinstance(exc.stderr, str) else ""
        return 124, stdout, stderr or "step timed out", True


def _step_host(step: dict, host_label: str | None = None) -> str | None:
    return host_label or step.get("host") or step.get("server")


def _write_json(path: Path, payload: dict, *, scrub=_no_scrub) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Scrub the serialized form so masking covers every nested field (step
    # stdout/stderr/cmd) regardless of structure — secrets never at rest.
    path.write_text(scrub(json.dumps(payload, separators=(",", ":"))), encoding="utf-8")
    path.chmod(0o600)


def _update_scope_state(scope_name: str, *, plan_id: str | None, run_id: str, rc: int, deployed_files: int, scrub=_no_scrub) -> None:
    state = load_scope_state(scope_name)
    payload = {
        "schema": "local81.state.v0.1",
        "scope": scope_name,
        "last_success": _now_iso() if rc == 0 else state.get("last_success"),
        "last_plan_id": plan_id,
        "last_run_id": run_id,
        "files_last_deployed_count": deployed_files,
    }
    _write_json(Path(".local81") / "state" / f"{scope_name}.json", payload, scrub=scrub)


def parse_hosts_file(path: str) -> list[dict]:
    hosts: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("#"):
            continue
        parts = [p.strip() for p in stripped.split("\t") if p.strip()]
        if len(parts) >= 3:
            hosts.append({"ip": parts[0], "server": parts[1], "alias": parts[2]})
        elif len(parts) >= 2:
            hosts.append({"ip": parts[0], "server": parts[1], "alias": parts[1]})
        elif parts:
            hosts.append({"ip": parts[0], "server": parts[0], "alias": parts[0]})
    return hosts


def _safe_print(*args, **kwargs) -> None:
    with _print_lock:
        print(*args, **kwargs)


def _drift_check(scopes: list, *, allow_drift: bool) -> tuple[list[str], list[str]]:
    """Re-gather facts for op-steps and report target drift from desired state.

    Returns ``(errors, warnings)``. For each step that carries desired-state
    metadata we probe the live target and classify the action deploy would take:

    * ``none``    — already converged (no output).
    * ``create``  — target absent; a normal first apply, not drift.
    * ``update``  — target exists but its content differs from the plan's intent.
      This is drift: an operator-visible divergence. It is an error by default so
      ``--check`` fails loudly; ``--allow-drift`` downgrades it to a warning.
    * ``unknown`` — the probe could not run (e.g. SSH unreachable). Fail-open:
      a warning, never a hard failure, mirroring the deploy-time gate.
    """
    errors: list[str] = []
    warnings: list[str] = []
    op_steps = [
        step
        for scope_item in scopes
        for step in scope_item.get("steps", [])
        if step.get("op") and isinstance(step.get("intent"), dict)
    ]
    if not op_steps:
        return errors, warnings
    counts = {"none": 0, "create": 0, "update": 0, "unknown": 0}
    drifted: list[str] = []
    unknowns: list[str] = []
    for step in op_steps:
        action, _observed = resolve_step_action(step)
        if action is None:
            continue
        counts[action] = counts.get(action, 0) + 1
        target = (step.get("intent") or {}).get("path", step.get("id", "?"))
        if action == "update":
            drifted.append(f"{step.get('id', '?')} ({target}) diverges from desired state")
        elif action == "unknown":
            unknowns.append(f"{step.get('id', '?')} ({target}) could not be probed")
    print("Desired-state drift:")
    print(f"  converged={counts['none']} create={counts['create']} "
          f"drift={counts['update']} unprobed={counts['unknown']}")
    for item in unknowns:
        warnings.append(f"could not probe target: {item}")
    for item in drifted:
        msg = f"target drift: {item}"
        if allow_drift:
            warnings.append(msg + " (allowed by --allow-drift)")
        else:
            errors.append(msg + " (re-plan, or override with --allow-drift)")
    print()
    return errors, warnings


def run_check(*, plan: str | None = None, use_latest: bool = False, scope: str | None = None,
              allow_drift: bool = False) -> int:
    plan_path = _resolve_plan_path(plan=plan, use_latest=use_latest)
    if plan_path is None or not plan_path.is_file():
        target = plan_path if plan_path is not None else Path(".local81/plans")
        print(f"Local-81 check could not find plan file: {target}")
        return 1
    try:
        plan_data = _load_plan(plan_path)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[fail] Plan file is not valid JSON: {exc}")
        return 1
    errors: list[str] = []
    warnings: list[str] = []
    print("Local-81 deploy --check")
    print("====================")
    print(f"Plan: {plan_path}\n")
    for key in ("kind", "mode", "schema", "plan_id", "scopes"):
        if key not in plan_data:
            errors.append(f"missing required key: {key}")
    if plan_data.get("kind") != "plan":
        errors.append(f"kind should be 'plan', got {plan_data.get('kind')!r}")
    if plan_data.get("mode") != "deploy":
        errors.append(f"mode should be 'deploy', got {plan_data.get('mode')!r}")
    current_config_path = None
    try:
        current_config_path = resolve_config_path()
    except FileNotFoundError:
        current_config_path = None
    for finding in plan_provenance_warnings(plan_data, current_config_path=current_config_path):
        warnings.append(finding.detail)
    scopes = plan_data.get("scopes", [])
    if scope and isinstance(scopes, list):
        scopes = [s for s in scopes if s.get("scope") == scope]
    if not scopes:
        if scope:
            errors.append(f"plan has no matching scope: {scope}")
        else:
            warnings.append("plan has no scopes")
    total_steps = 0
    for scope_item in scopes:
        scope_name = scope_item.get("scope", "(unnamed)")
        steps = scope_item.get("steps", [])
        total_steps += len(steps)
        if not steps:
            warnings.append(f"scope '{scope_name}' has no steps")
        for step in steps:
            if "id" not in step:
                errors.append(f"step in scope '{scope_name}' missing 'id'")
            if "cmd" not in step:
                errors.append(f"step '{step.get('id', '?')}' missing 'cmd'")
    for tool in ("bash", "ssh", "rsync"):
        found = shutil.which(tool)
        if found:
            print(f"[ok] {tool}: {found}")
        elif tool == "bash":
            errors.append(f"{tool} not found")
            print(f"[fail] {tool}: not found in PATH")
        else:
            warnings.append(f"{tool} not found")
            print(f"[warn] {tool}: not found in PATH")
    for finding in validate_config():
        if finding.level == "FAIL":
            errors.append(finding.render())
        elif finding.level == "WARN":
            warnings.append(finding.render())
    print(f"\nScopes: {len(scopes)}")
    print(f"Total steps: {total_steps}\n")
    drift_errors, drift_warnings = _drift_check(scopes if isinstance(scopes, list) else [], allow_drift=allow_drift)
    errors.extend(drift_errors)
    warnings.extend(drift_warnings)
    for err in errors:
        print(f"[fail] {err}")
    for warn in warnings:
        print(f"[warn] {warn}")
    scoped_plan_data = {**plan_data, "scopes": scopes if isinstance(scopes, list) else []}
    policy_findings = enforce_deploy_policy(scoped_plan_data)
    for finding in policy_findings:
        if finding.level == "FAIL":
            errors.append(finding.render())
            print(f"[fail] {finding.render()}")
        elif finding.level == "WARN":
            warnings.append(finding.render())
            print(f"[warn] {finding.render()}")
    execution_findings = summarize_execution_risks(scoped_plan_data)
    if execution_findings:
        print("Execution safety diagnostics:")
    for finding in execution_findings:
        warnings.append(finding.render())
        print(f"[warn] {finding.render()}")
    print()
    if errors:
        print(f"Check failed with {len(errors)} error(s).")
        return 1
    print(f"Check passed{' with ' + str(len(warnings)) + ' warning(s)' if warnings else '. Plan is valid and ready to deploy.'}")
    return 0


def _maybe_notify(notifications: list[str], config: dict, *, quiet: bool, force: bool, event: NotificationEvent) -> None:
    if quiet:
        return
    send_on_success = bool(config.get("notify_on_success")) or force
    if event.status == "success" and not send_on_success and event.kind == "deploy":
        return
    notifications.extend(notify_all(config, event))

def _run_step(scope_name: str, step: dict, *, dry_run: bool, step_timeout: int | None,
              plan_data: dict, host_label: str | None = None,
              notifications_config: dict | None = None,
              notifications_log: list[str] | None = None,
              quiet: bool = False, force_notify: bool = False,
              run_id: str = "", become: Become = _BECOME_DISABLED) -> dict:
    target_host = _step_host(step, host_label)
    step_record: dict = {
        "scope": scope_name,
        "id": step["id"],
        "type": step.get("type"),
        "host": target_host,
        "cmd": step.get("cmd"),
    }
    # Persist reversibility so a completed run can be rolled back after the fact
    # (local81 rollback <run-id>), not only during an on-failure rollback.
    _rollback = step.get("rollback") or {}
    if _rollback.get("cmd"):
        step_record["rollback"] = {"type": _rollback.get("type"), "cmd": _rollback["cmd"]}
        step_record["reversible"] = True
    else:
        step_record["reversible"] = False
    started = _now_iso()
    started_monotonic = monotonic()
    # Desired-state gate: on live runs, probe the target and skip op-steps that
    # are already converged. Opt-in (raw-command steps return action=None) and
    # fail-open (probe errors -> action 'unknown' -> the step still runs).
    if not dry_run:
        action, observed = resolve_step_action(step)
        if action is not None:
            step_record["action"] = action
            step_record["observed_state"] = observed
            if action == "none":
                step_record.update({
                    "rc": 0,
                    "started_at": started,
                    "finished_at": _now_iso(),
                    "stdout": "",
                    "stderr": "",
                    "duration_seconds": monotonic() - started_monotonic,
                    "converged": True,
                })
                return step_record
    timeout_seconds = step.get("timeout")
    if timeout_seconds is None:
        timeout_seconds = step_timeout
    # Privilege escalation is opt-in per step (``"become": true``) and only
    # actually elevates when the run carries a become context. The password (if
    # any) goes to sudo on stdin; the recorded cmd stays the un-wrapped form.
    step_become = become if step.get("become") else _BECOME_DISABLED
    effective_cmd = step_become.wrap(step["cmd"])
    stdin_input = step_become.stdin
    if step_become.enabled:
        step_record["become"] = True
    if step.get("type") == "remote_cmd" and target_host:
        step_rc, stdout, stderr, timed_out = _run_remote(target_host, effective_cmd, timeout_seconds=timeout_seconds, dry_run=dry_run, stdin_input=stdin_input)
    else:
        step_rc, stdout, stderr, timed_out = _run_shell(effective_cmd, timeout_seconds=timeout_seconds, dry_run=dry_run, stdin_input=stdin_input)
    finished = _now_iso()
    duration = monotonic() - started_monotonic
    step_record.update({"rc": step_rc, "started_at": started, "finished_at": finished, "stdout": stdout, "stderr": stderr, "duration_seconds": duration})
    if timed_out and notifications_config is not None and notifications_log is not None:
        _maybe_notify(notifications_log, notifications_config, quiet=quiet, force=True, event=NotificationEvent(
            host=target_host or "local",
            status="warning",
            duration_seconds=duration,
            errors=[stderr or "step timed out", f"step={step['id']}", f"scope={scope_name}"],
            run_id=run_id,
            plan_id=plan_data.get("plan_id"),
            scope=scope_name,
            kind="timeout-alert",
        ))
    return step_record


def _write_rollback_record(run_dir: Path | None, record: dict, *, scrub=_no_scrub) -> None:
    if run_dir is None:
        return
    rollback_log = run_dir / "rollback.log"
    with rollback_log.open("a", encoding="utf-8") as fh:
        fh.write(scrub(json.dumps(record, separators=(",", ":"))) + "\n")
    rollback_log.chmod(0o600)


def _run_rollbacks(successful_with_rollback: list[dict], *, dry_run: bool,
                   run_dir: Path | None, printer=print, scrub=_no_scrub) -> None:
    for prev in reversed(successful_with_rollback):
        rollback = prev.get("rollback") or {}
        if not rollback.get("cmd"):
            continue
        started = _now_iso()
        rollback_rc, rollback_stdout, rollback_stderr, _timed_out = _run_shell(rollback["cmd"], dry_run=dry_run)
        finished = _now_iso()
        _write_rollback_record(run_dir, {
            "step_id": prev.get("id"),
            "rollback_type": rollback.get("type"),
            "cmd": rollback.get("cmd"),
            "rc": rollback_rc,
            "started_at": started,
            "finished_at": finished,
            "stdout": rollback_stdout,
            "stderr": rollback_stderr,
        }, scrub=scrub)
        if rollback_rc != 0:
            printer(f"  !! Rollback failed with rc={rollback_rc}: {prev.get('id')}")
            if rollback_stderr:
                printer(f"     {rollback_stderr}")


def _record_skipped_step(scope_name: str, step: dict, host_label: str | None) -> dict:
    return {
        "scope": scope_name,
        "id": step["id"],
        "type": step.get("type"),
        "host": _step_host(step, host_label),
        "cmd": step.get("cmd"),
        "rc": -1,
        "started_at": _now_iso(),
        "finished_at": _now_iso(),
        "stdout": "",
        "stderr": "skipped after earlier failure",
    }


def _handle_step_failure(step: dict, step_record: dict, *, successful_with_rollback: list[dict],
                         dry_run: bool, rollback_on_failure: bool, plan_data: dict,
                         run_dir: Path | None, printer=print, scrub=_no_scrub) -> int:
    step_rc = step_record["rc"]
    stderr = step_record.get("stderr", "")
    printer(f"  !! Step failed with rc={step_rc}: {step['id']}")
    if stderr:
        printer(f"     {stderr}")
    on_failure = step.get("on_failure") or {}
    if on_failure.get("cmd"):
        _run_shell(on_failure["cmd"], dry_run=dry_run)
    if rollback_on_failure:
        _run_rollbacks(successful_with_rollback, dry_run=dry_run, run_dir=run_dir, printer=printer, scrub=scrub)
    global_failure = plan_data.get("on_failure") or {}
    if global_failure.get("cmd"):
        _run_shell(global_failure["cmd"], dry_run=dry_run)
    return step_rc or 1


def _deploy_scope_steps(scope_obj: dict, *, dry_run: bool, step_timeout: int | None,
                        fail_fast: bool, rollback_on_failure: bool, plan_data: dict,
                        host_label: str | None = None, printer=print,
                        notifications_config: dict | None = None,
                        notifications_log: list[str] | None = None,
                        quiet: bool = False, force_notify: bool = False,
                        run_id: str = "", run_dir: Path | None = None,
                        max_parallel: int = 1, scrub=_no_scrub,
                        become: Become = _BECOME_DISABLED) -> tuple[list[dict], int, int]:
    scope_name = scope_obj.get("scope", "unknown")
    steps_out: list[dict] = []
    successful_with_rollback: list[dict] = []
    failure_seen = False
    rc = 0
    deployed_files = 0
    display = f"Scope {scope_name}"
    if host_label:
        display += f" -> {host_label}"
    printer(display)
    printer(f"  Steps queued: {len(scope_obj.get('steps', []))}")
    steps = scope_obj.get("steps", [])
    index = 0
    while index < len(steps):
        if failure_seen and fail_fast:
            steps_out.append(_record_skipped_step(scope_name, steps[index], host_label))
            index += 1
            continue
        step = steps[index]
        if step.get("parallel") and max_parallel > 1:
            parallel_batch: list[tuple[int, dict]] = []
            while index < len(steps) and steps[index].get("parallel"):
                parallel_step = steps[index]
                printer(f"  -> {parallel_step['id']} [{parallel_step.get('type', 'step')}] on {_step_host(parallel_step, host_label) or 'n/a'}")
                parallel_batch.append((index, parallel_step))
                index += 1
            batch_results: dict[int, dict] = {}
            workers = min(max_parallel, len(parallel_batch))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        _run_step,
                        scope_name,
                        parallel_step,
                        dry_run=dry_run,
                        step_timeout=step_timeout,
                        plan_data=plan_data,
                        host_label=host_label,
                        notifications_config=notifications_config,
                        notifications_log=notifications_log,
                        quiet=quiet,
                        force_notify=force_notify,
                        run_id=run_id,
                        become=become,
                    ): batch_index
                    for batch_index, parallel_step in parallel_batch
                }
                for future in as_completed(futures):
                    batch_results[futures[future]] = future.result()
            for batch_index, parallel_step in parallel_batch:
                step_record = batch_results[batch_index]
                steps_out.append(step_record)
                if step_record["rc"] == 0:
                    if parallel_step.get("type") == "rsync" and not step_record.get("converged"):
                        deployed_files += 1
                    if parallel_step.get("rollback") and parallel_step["rollback"].get("cmd"):
                        successful_with_rollback.append(parallel_step)
                    continue
                if not failure_seen:
                    rc = _handle_step_failure(
                        parallel_step,
                        step_record,
                        successful_with_rollback=successful_with_rollback,
                        dry_run=dry_run,
                        rollback_on_failure=rollback_on_failure,
                        plan_data=plan_data,
                        run_dir=run_dir,
                        printer=printer,
                        scrub=scrub,
                    )
                failure_seen = True
            if failure_seen and fail_fast:
                for remaining in steps[index:]:
                    steps_out.append(_record_skipped_step(scope_name, remaining, host_label))
                break
            continue
        target_host = _step_host(step, host_label)
        printer(f"  -> {step['id']} [{step.get('type', 'step')}] on {target_host or 'n/a'}")
        step_record = _run_step(
            scope_name,
            step,
            dry_run=dry_run,
            step_timeout=step_timeout,
            plan_data=plan_data,
            host_label=host_label,
            notifications_config=notifications_config,
            notifications_log=notifications_log,
            quiet=quiet,
            force_notify=force_notify,
            run_id=run_id,
            become=become,
        )
        steps_out.append(step_record)
        if step_record["rc"] == 0:
            if step.get("type") == "rsync" and not step_record.get("converged"):
                deployed_files += 1
            if step.get("rollback") and step["rollback"].get("cmd"):
                successful_with_rollback.append(step)
            index += 1
            continue
        failure_seen = True
        rc = _handle_step_failure(
            step,
            step_record,
            successful_with_rollback=successful_with_rollback,
            dry_run=dry_run,
            rollback_on_failure=rollback_on_failure,
            plan_data=plan_data,
            run_dir=run_dir,
            printer=printer,
            scrub=scrub,
        )
        if fail_fast:
            for remaining in steps[index + 1:]:
                steps_out.append(_record_skipped_step(scope_name, remaining, host_label))
            break
        index += 1
    return steps_out, rc, deployed_files


def _group_steps_by_host(scopes: list[dict]) -> OrderedDict[str, list[dict]]:
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for scope_obj in scopes:
        scope_name = scope_obj.get("scope", "unknown")
        for step in scope_obj.get("steps", []):
            host = step.get("host", "local")
            groups.setdefault(host, []).append({**step, "_scope": scope_name})
    return groups


def _deploy_host_group(host: str, steps: list[dict], *, dry_run: bool,
                       step_timeout: int | None, fail_fast: bool,
                       rollback_on_failure: bool, plan_data: dict,
                       notifications_config: dict | None, notifications_log: list[str],
                       quiet: bool, force_notify: bool, run_id: str,
                       run_dir: Path | None, max_parallel: int, scrub=_no_scrub,
                       become: Become = _BECOME_DISABLED) -> dict:
    scope_obj = {"scope": steps[0].get("_scope", "unknown"), "steps": steps}
    steps_out, rc, deployed_files = _deploy_scope_steps(
        scope_obj, dry_run=dry_run, step_timeout=step_timeout,
        fail_fast=fail_fast, rollback_on_failure=rollback_on_failure,
        plan_data=plan_data, host_label=host, printer=_safe_print,
        notifications_config=notifications_config, notifications_log=notifications_log,
        quiet=quiet, force_notify=force_notify, run_id=run_id,
        run_dir=run_dir, max_parallel=max_parallel, scrub=scrub, become=become,
    )
    return {"host": host, "rc": rc, "deployed_files": deployed_files, "steps": steps_out}


def _write_host_log(run_dir: Path | None, host: str, steps: list[dict], *, scrub=_no_scrub) -> None:
    """Write a per-host run log so operators can ``logs <run-id> --host <h>``."""
    if run_dir is None:
        return
    safe = host.replace("/", "_").replace(":", "_")
    log_path = run_dir / f"{safe}.log"
    lines = [f"host={host}", ""]
    for step in steps:
        tag = "OK" if step.get("rc", -1) == 0 else ("SKIP" if step.get("rc") == -1 else "FAIL")
        lines.append(f"[{tag}] {step.get('id', '?')} [{step.get('type', 'step')}]")
        if step.get("stderr"):
            lines.append(f"  stderr: {step['stderr']}")
    log_path.write_text(scrub("\n".join(lines) + "\n"), encoding="utf-8")
    log_path.chmod(0o600)


def _apply_limit(groups: "OrderedDict[str, list[dict]]", limit: str | None) -> "OrderedDict[str, list[dict]]":
    """Filter host groups by a glob ``--limit`` pattern (fnmatch)."""
    if not limit:
        return groups
    filtered: OrderedDict[str, list[dict]] = OrderedDict()
    for host, steps in groups.items():
        if fnmatch.fnmatch(host, limit):
            filtered[host] = steps
    return filtered


def _run_fleet_deploy(scopes: list[dict], *, dry_run: bool, step_timeout: int | None,
                      fail_fast: bool, rollback_on_failure: bool, plan_data: dict,
                      notifications_cfg: dict, notification_warnings: list[str],
                      quiet: bool, notify: bool, run_id: str, run_dir: Path | None,
                      max_parallel: int, forks: int, serial: str | None,
                      max_fail: str | None, limit: str | None, scrub=_no_scrub,
                      become: Become = _BECOME_DISABLED) -> tuple[list[dict], int, list[dict]]:
    """Schedule the plan's hosts in rolling batches with a failure threshold.

    Derives the fleet from the distinct ``host`` of each plan step, applies the
    ``--limit`` filter, then drives :func:`run_fleet` with a runner that pushes
    one host's steps and maps its result onto the changed/unchanged/failed
    taxonomy. Returns ``(all_steps, overall_rc, host_results)``.
    """
    groups = _apply_limit(_group_steps_by_host(scopes), limit)
    hosts = list(groups.keys())
    if not hosts:
        print("Local-81 fleet deploy matched no hosts (check --limit).")
        return [], 1, []

    def runner(host: str) -> HostOutcome:
        result = _deploy_host_group(
            host, groups[host], dry_run=dry_run, step_timeout=step_timeout,
            fail_fast=fail_fast, rollback_on_failure=rollback_on_failure,
            plan_data=plan_data, notifications_config=notifications_cfg,
            notifications_log=notification_warnings, quiet=quiet,
            force_notify=notify, run_id=run_id, run_dir=run_dir,
            max_parallel=max_parallel, scrub=scrub, become=become,
        )
        _write_host_log(run_dir, host, result["steps"], scrub=scrub)
        status = classify(result["rc"], result["deployed_files"])
        return HostOutcome(host=host, status=status, rc=result["rc"],
                           changed_count=result["deployed_files"], detail=result)

    fleet = run_fleet(hosts, runner, forks=forks, serial=serial, max_fail=max_fail)

    all_steps: list[dict] = []
    host_results: list[dict] = []
    for outcome in fleet.outcomes:
        detail = outcome.detail or {}
        all_steps.extend(detail.get("steps", []))
        host_results.append({
            "host": outcome.host,
            "rc": outcome.rc,
            "deployed_files": outcome.changed_count,
            "status": outcome.status,
        })
    print()
    print(render_summary(fleet))
    return all_steps, fleet.rc, host_results


def run_deploy(*, plan: str | None = None, use_latest: bool = False, scope: str | None = None, max_parallel: int = 1,
               rollback_on_failure: bool = False, step_timeout: int | None = None,
               fail_fast: bool = False, dry_run: bool = False,
               hosts_file: str | None = None, parallel: bool = False,
               check: bool = False, allow_drift: bool = False, profile: str | None = None,
               notify: bool = False, quiet: bool = False,
               forks: int | None = None, serial: str | None = None,
               max_fail: str | None = None, limit: str | None = None,
               resolver: SecretResolver | None = None) -> int:
    if check:
        return run_check(plan=plan, use_latest=use_latest, scope=scope, allow_drift=allow_drift)
    # One scrubber per run: every secret the resolver hands out during this
    # deploy is masked out of run.json, host logs and rollback logs before they
    # touch disk. With no secrets resolved, scrub is an identity no-op.
    resolver = resolver or SecretResolver()
    scrub = resolver.scrub
    plan_path = _resolve_plan_path(plan=plan, use_latest=use_latest)
    if plan_path is None or not plan_path.is_file():
        target = plan_path if plan_path is not None else Path(".local81/plans")
        print(f"Local-81 deploy could not find plan file: {target}")
        return 1
    plan_data = _load_plan(plan_path)
    # Optional privilege escalation. A plan-level ``become`` block enables sudo;
    # ``password_ref`` (a secret reference, never a literal) is resolved here in
    # memory via the same resolver, so the password is scrubbed from artifacts.
    _become_cfg = plan_data.get("become") or {}
    become_ctx = resolve_become(
        resolver,
        password_ref=_become_cfg.get("password_ref"),
        enabled=bool(_become_cfg),
        user=_become_cfg.get("user"),
    )
    scopes = plan_data.get("scopes", [])
    if scope:
        scopes = [s for s in scopes if s.get("scope") == scope]
    if not scopes:
        print("Local-81 deploy did not find any matching scopes in that plan.")
        return 1
    scoped_plan_data = {**plan_data, "scopes": scopes}
    policy_findings = enforce_deploy_policy(scoped_plan_data)
    blocking_policy_findings = [finding for finding in policy_findings if finding.level == "FAIL"]
    if blocking_policy_findings:
        print("Local-81 deploy blocked by access-control policy:")
        for finding in blocking_policy_findings:
            print(f"  {finding.render()}")
        return 1
    notifications_cfg = {}
    try:
        resolve_config_path()
        notifications_cfg = load_config(profile=profile).notifications
    except FileNotFoundError:
        notifications_cfg = {}
    hosts = None
    if hosts_file:
        hosts = parse_hosts_file(hosts_file)
        if not hosts:
            print(f"Local-81 deploy found no hosts in {hosts_file}")
            return 1
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-deploy"
    run_dir = Path(".local81") / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run_dir.chmod(0o700)
    run_json = run_dir / "run.json"
    run_log = run_dir / "run.log"
    run_log.write_text(
        f"run_id={run_id}\nplan_id={plan_data.get('plan_id', '')}\ndry_run={'true' if dry_run else 'false'}\n",
        encoding="utf-8",
    )
    run_log.chmod(0o600)
    all_steps: list[dict] = []
    overall_rc = 0
    host_results: list[dict] = []
    notification_warnings: list[str] = []
    deploy_started = monotonic()
    print("Local-81 deploy")
    print("============")
    print(f"Plan: {plan_path}")
    print(f"Scopes: {', '.join(s.get('scope', '(unknown)') for s in scopes)}")
    print(f"Mode: {'dry run' if dry_run else 'live run'}")
    if profile:
        print(f"Profile: {profile}")
    if hosts:
        print(f"Hosts: {', '.join(h['alias'] for h in hosts)}")
        print(f"Parallel: {'yes' if parallel else 'no'}")
    print(f"Max parallel requested: {max_parallel}\n")
    hook_env = {
        **{k: v for k, v in __import__('os').environ.items()},
        "LOCAL81_PLAN": str(plan_path),
        "LOCAL81_RUN_ID": run_id,
        "LOCAL81_PROFILE": profile or "",
    }
    pre_rc, pre_out, pre_err = run_hook("pre-deploy.sh", env=hook_env)
    if pre_out:
        print(pre_out)
    if pre_err:
        print(pre_err)
    if pre_rc != 0:
        print("Pre-deploy hook failed, aborting deploy.")
        return pre_rc
    fleet_requested = forks is not None or serial is not None or max_fail is not None or limit is not None
    if fleet_requested:
        effective_forks = forks if forks is not None else 5
        all_steps, overall_rc, host_results = _run_fleet_deploy(
            scopes, dry_run=dry_run, step_timeout=step_timeout, fail_fast=fail_fast,
            rollback_on_failure=rollback_on_failure, plan_data=plan_data,
            notifications_cfg=notifications_cfg, notification_warnings=notification_warnings,
            quiet=quiet, notify=notify, run_id=run_id, run_dir=run_dir,
            max_parallel=max_parallel, forks=effective_forks, serial=serial,
            max_fail=max_fail, limit=limit, scrub=scrub, become=become_ctx,
        )
        if not dry_run and overall_rc == 0:
            for scope_obj in scopes:
                _update_scope_state(scope_obj.get("scope", "unknown"), plan_id=plan_data.get("plan_id"), run_id=run_id, rc=overall_rc, deployed_files=sum(hr["deployed_files"] for hr in host_results), scrub=scrub)
    elif hosts:
        host_aliases = {h["alias"] for h in hosts}
        host_aliases.update(h["server"] for h in hosts)
        host_aliases.update(h["ip"] for h in hosts)
        step_groups = _group_steps_by_host(scopes)
        filtered_groups: OrderedDict[str, list[dict]] = OrderedDict()
        for host_key, host_steps in step_groups.items():
            if host_key in host_aliases:
                filtered_groups[host_key] = host_steps
        if not filtered_groups:
            for h in hosts:
                for scope_obj in scopes:
                    for step in scope_obj.get("steps", []):
                        filtered_groups.setdefault(h["alias"], []).append({**step, "_scope": scope_obj.get("scope", "unknown"), "host": h["alias"]})
        if parallel and len(filtered_groups) > 1:
            workers = max_parallel if max_parallel > 1 else len(filtered_groups)
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_deploy_host_group, host_key, host_steps, dry_run=dry_run, step_timeout=step_timeout, fail_fast=fail_fast, rollback_on_failure=rollback_on_failure, plan_data=plan_data, notifications_config=notifications_cfg, notifications_log=notification_warnings, quiet=quiet, force_notify=notify, run_id=run_id, run_dir=run_dir, max_parallel=max_parallel, scrub=scrub, become=become_ctx): host_key for host_key, host_steps in filtered_groups.items()}
                for future in as_completed(futures):
                    result = future.result()
                    all_steps.extend(result["steps"])
                    host_results.append({"host": result["host"], "rc": result["rc"], "deployed_files": result["deployed_files"]})
                    if result["rc"] != 0:
                        overall_rc = result["rc"]
                        if fail_fast:
                            break
        else:
            for host_key, host_steps in filtered_groups.items():
                result = _deploy_host_group(host_key, host_steps, dry_run=dry_run, step_timeout=step_timeout, fail_fast=fail_fast, rollback_on_failure=rollback_on_failure, plan_data=plan_data, notifications_config=notifications_cfg, notifications_log=notification_warnings, quiet=quiet, force_notify=notify, run_id=run_id, run_dir=run_dir, max_parallel=max_parallel, scrub=scrub, become=become_ctx)
                all_steps.extend(result["steps"])
                host_results.append({"host": result["host"], "rc": result["rc"], "deployed_files": result["deployed_files"]})
                if result["rc"] != 0:
                    overall_rc = result["rc"]
                    if fail_fast:
                        break
                print()
        print("\nPer-host results:")
        for hr in host_results:
            status = "ok" if hr["rc"] == 0 else f"FAILED (rc={hr['rc']})"
            print(f"  {hr['host']}: {status} ({hr['deployed_files']} files)")
        if not dry_run and overall_rc == 0:
            for scope_obj in scopes:
                _update_scope_state(scope_obj.get("scope", "unknown"), plan_id=plan_data.get("plan_id"), run_id=run_id, rc=overall_rc, deployed_files=sum(hr["deployed_files"] for hr in host_results), scrub=scrub)
    else:
        for scope_obj in scopes:
            scope_name = scope_obj.get("scope", "unknown")
            steps_out, rc, deployed_files = _deploy_scope_steps(scope_obj, dry_run=dry_run, step_timeout=step_timeout, fail_fast=fail_fast, rollback_on_failure=rollback_on_failure, plan_data=plan_data, notifications_config=notifications_cfg, notifications_log=notification_warnings, quiet=quiet, force_notify=notify, run_id=run_id, run_dir=run_dir, max_parallel=max_parallel, scrub=scrub, become=become_ctx)
            all_steps.extend(steps_out)
            if not dry_run and rc == 0:
                _update_scope_state(scope_name, plan_id=plan_data.get("plan_id"), run_id=run_id, rc=rc, deployed_files=deployed_files, scrub=scrub)
            if rc != 0:
                overall_rc = rc
            if rc != 0 and fail_fast:
                break
            print()
    duration = monotonic() - deploy_started
    payload: dict = {"schema": "local81.run.v0.1", "run_id": run_id, "plan_id": plan_data.get("plan_id"), "started_at": all_steps[0]["started_at"] if all_steps else _now_iso(), "finished_at": _now_iso(), "rc": overall_rc, "dry_run": dry_run, "steps": all_steps, "profile": profile, "notification_warnings": notification_warnings}
    if host_results:
        payload["hosts"] = host_results
    _write_json(run_json, payload, scrub=scrub)
    event = NotificationEvent(host=",".join(hr["host"] for hr in host_results) if host_results else (scope or scopes[0].get("scope", "local")), status="success" if overall_rc == 0 else "failed", duration_seconds=duration, errors=[scrub(s["stderr"]) for s in all_steps if s.get("stderr") and s.get("rc") not in (0, -1)], run_id=run_id, plan_id=plan_data.get("plan_id"), scope=scope)
    _maybe_notify(notification_warnings, notifications_cfg, quiet=quiet, force=notify, event=event)
    post_rc, post_out, post_err = run_hook("post-deploy.sh", env={**hook_env, "LOCAL81_DEPLOY_RC": str(overall_rc)})
    if post_out:
        print(post_out)
    if post_err:
        print(post_err)
    if post_rc != 0:
        print(f"[warn] post-deploy hook failed with rc={post_rc}")
    for warning in notification_warnings:
        print(f"[warn] {warning}")
    if overall_rc == 0:
        print("Deploy finished cleanly.")
        print(f"Run record: {run_json}")
        return 0
    print("Deploy finished with errors.")
    print(f"Run record: {run_json}")
    print("Review the failing step above, fix the underlying issue, then rerun deploy or generate a fresh plan.")
    return overall_rc
