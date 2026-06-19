from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from local81.config import load_config, validate_config
from local81.hooks import list_hooks
from local81.policy import compliance_findings
from local81.profiles import list_profiles, load_profile_data


@dataclass(slots=True)
class CheckResult:
    level: str
    name: str
    detail: str

    def render(self) -> str:
        icon = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}[self.level]
        return f"{icon} {self.name}: {self.detail}"


def _binary_check(name: str, *, required: bool = True) -> CheckResult:
    resolved = shutil.which(name)
    if resolved:
        return CheckResult("PASS", f"binary:{name}", resolved)
    return CheckResult("FAIL" if required else "WARN", f"binary:{name}", "missing from PATH")


def _dir_check(path_str: str) -> CheckResult:
    path = Path(path_str).expanduser()
    if not path.exists():
        return CheckResult("WARN", f"dir:{path_str}", "does not exist yet")
    if not path.is_dir():
        return CheckResult("FAIL", f"dir:{path_str}", "exists but is not a directory")
    try:
        probe = path / ".local81-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return CheckResult("PASS", f"dir:{path_str}", "exists and is writable")
    except Exception:
        return CheckResult("WARN", f"dir:{path_str}", "exists but is not writable")


def _plan_checks(plan_path: Path) -> list[CheckResult]:
    try:
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        results = [CheckResult("PASS", "plan:json", str(plan_path))]
    except Exception as exc:
        return [CheckResult("FAIL", "plan:json", str(exc))]
    required_keys = ["kind", "mode", "schema", "plan_id", "scopes"]
    missing = [key for key in required_keys if key not in data]
    results.append(CheckResult("FAIL" if missing else "PASS", "plan:schema", f"missing keys: {', '.join(missing)}" if missing else "required keys present"))
    results.append(CheckResult("PASS" if data.get("kind") == "plan" else "FAIL", "plan:kind", f"got {data.get('kind')!r}"))
    results.append(CheckResult("PASS" if data.get("mode") == "deploy" else "FAIL", "plan:mode", f"got {data.get('mode')!r}"))
    results.append(CheckResult("PASS" if data.get("schema") in {"local81.plan.v2", "local81.plan.v0.1"} else "FAIL", "plan:schema_ver", f"got {data.get('schema')!r}"))
    scopes = data.get("scopes", [])
    results.append(CheckResult("PASS" if isinstance(scopes, list) and scopes else "WARN", "plan:scopes", f"count={len(scopes) if isinstance(scopes, list) else 0}"))
    total_steps = sum(len(scope.get("steps", [])) for scope in scopes if isinstance(scope, dict)) if isinstance(scopes, list) else 0
    results.append(CheckResult("PASS" if total_steps > 0 else "WARN", "plan:steps", f"count={total_steps}"))
    return results


def _config_checks(profile: str | None) -> list[CheckResult]:
    results: list[CheckResult] = []
    validation_results = validate_config()
    for finding in validation_results:
        results.append(CheckResult(finding.level, finding.name, finding.detail))
    profile_checked = False
    if profile:
        try:
            load_profile_data(profile)
            results.append(CheckResult("PASS", "config:profile", profile))
        except FileNotFoundError as exc:
            results.append(CheckResult("FAIL", "config:profile", f"missing profile: {exc}"))
        except Exception as exc:
            results.append(CheckResult("FAIL", "config:profile", str(exc)))
        profile_checked = True
    if any(finding.level == "FAIL" for finding in validation_results):
        return results
    try:
        cfg = load_config(profile=profile)
        results.append(CheckResult("PASS", "config:load", f"project={cfg.project}"))
        results.append(CheckResult("PASS" if cfg.scopes else "WARN", "config:scopes", f"count={len(cfg.scopes)}"))
        if not profile_checked:
            results.append(CheckResult("PASS", "config:profile", profile or "base"))
    except FileNotFoundError as exc:
        results.append(CheckResult("WARN", "config:load", f"missing config: {exc}"))
        return results
    except Exception as exc:
        results.append(CheckResult("FAIL", "config:load", str(exc)))
        return results
    for hook in list_hooks():
        if hook.exists and not hook.executable:
            results.append(CheckResult("WARN", f"hook:{hook.name}", "present but not executable"))
    profile_names = list_profiles()
    results.append(CheckResult("PASS", "profiles:count", str(len(profile_names))))
    return results


def _secrets_checks() -> list[CheckResult]:
    """Summarise managed secret references and prove they parse.

    Local-81 stores references (``env://``/``bao://``/``sops://``), never
    literals. Loading the db config already rejects malformed refs, so a clean
    load is positive evidence; we surface the count so an operator can see at a
    glance that indirection is in use.
    """
    from local81.db.config import DatabaseConfigError, load_database_targets
    from local81.secrets import is_secret_ref

    try:
        targets = load_database_targets()
    except FileNotFoundError:
        return []
    except DatabaseConfigError as exc:
        return [CheckResult("FAIL", "secrets:refs", str(exc))]
    ref_count = sum(
        1
        for target in targets
        for value in target.settings.values()
        if isinstance(value, str) and is_secret_ref(value)
    )
    return [CheckResult("PASS", "secrets:refs", f"{ref_count} managed reference(s) parse cleanly")]


def _fleet_checks(profile: str | None) -> list[CheckResult]:
    """Live, read-only probe of every host in the configured scopes.

    Off by default (network I/O); enabled by ``doctor --fleet``. Confirms each
    target is key-reachable over SSH and carries python3/rsync/sha256sum — the
    only things local81 needs on an endpoint.
    """
    from local81.onboarding import probe_host

    try:
        config = load_config(profile=profile)
    except FileNotFoundError:
        return [CheckResult("WARN", "fleet:config", "no config; nothing to probe")]
    ssh_opts = config.ssh_options()
    hosts: list[str] = []
    for scope in config.scopes:
        if not scope.enabled:
            continue
        for server in scope.servers:
            if server and server not in hosts:
                hosts.append(server)
    if not hosts:
        return [CheckResult("WARN", "fleet:hosts", "no servers configured in any scope")]
    results: list[CheckResult] = []
    for host in hosts:
        probe = probe_host(host, ssh_opts)
        if probe.ready:
            results.append(CheckResult("PASS", f"fleet:{host}", f"ready — {probe.python3}, {probe.rsync}"))
        elif probe.key_authed:
            missing = [n for n, ok in (("python3", probe.python3), ("rsync", probe.rsync), ("sha256sum", probe.sha256sum)) if not ok]
            results.append(CheckResult("WARN", f"fleet:{host}", f"key OK but missing {', '.join(missing)}"))
        elif probe.tcp_open:
            results.append(CheckResult("WARN", f"fleet:{host}", "reachable but not key-authed (run: local81 onboard)"))
        else:
            results.append(CheckResult("FAIL", f"fleet:{host}", f"unreachable ({probe.error or 'no tcp/22'})"))
    return results


def run_doctor(plan: str | None = None, profile: str | None = None, fleet: bool = False) -> int:
    checks: list[CheckResult] = [
        _binary_check("bash"),
        _binary_check("python3"),
        _binary_check("ssh"),
        _binary_check("rsync"),
        _binary_check("find"),
        _binary_check("sha256sum"),
        _binary_check("git", required=False),
        _dir_check("~/.local81"),
        _dir_check(".local81"),
        _dir_check(".local81/plans"),
        _dir_check(".local81/runs"),
        _dir_check(".local81/state"),
    ]
    checks.extend(_config_checks(profile))
    checks.extend(_secrets_checks())
    for finding in compliance_findings():
        checks.append(CheckResult(finding.level, f"policy:{finding.control}", finding.detail))
    if plan:
        checks.extend(_plan_checks(Path(plan)))
    if fleet:
        checks.extend(_fleet_checks(profile))
    passes = [c for c in checks if c.level == "PASS"]
    warns = [c for c in checks if c.level == "WARN"]
    fails = [c for c in checks if c.level == "FAIL"]
    print("Local-81 doctor")
    print("============")
    print(f"Checked {len(checks)} items: {len(passes)} ok, {len(warns)} warnings, {len(fails)} failures.\n")
    for title, bucket in (("Ready", passes), ("Needs attention", warns), ("Blocking issues", fails)):
        if not bucket:
            continue
        print(f"{title}:")
        for check in bucket:
            print(f"  {check.render()}")
        print()
    if fails:
        print("Doctor found blocking issues. Fix those first, then run 'local81 doctor' again.")
        return 1
    if warns:
        print("Doctor finished with warnings. You can keep going, but it is worth cleaning these up.")
        return 0
    print("Everything looks ready for the next step.")
    return 0
