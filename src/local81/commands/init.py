from __future__ import annotations

import json
import re
import shutil
import sys
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True)
class LegacyScope:
    name: str
    values: dict[str, str]


_MODERN_SCOPE_RE = re.compile(r'^\s*\[scope\s+"([^"]+)"\]\s*$')
_SECTION_RE = re.compile(r"^\s*\[[^\]]+\]\s*$")


def _warn(message: str) -> None:
    print(f"local81: warning: {message}", file=sys.stderr)


def _err(message: str) -> None:
    print(f"local81: {message}", file=sys.stderr)


def _parse_bool(value: str) -> bool | None:
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return None


def _normalize_servers(raw: str) -> str:
    tokens = raw.replace(",", " ").split()
    return ",".join(tokens)


def _parse_legacy(path: Path) -> list[LegacyScope]:
    scopes: OrderedDict[str, dict[str, str]] = OrderedDict()
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1].strip()
            if name not in scopes:
                scopes[name] = {}
            current = name
            continue
        if current is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key not in scopes[current]:
            scopes[current][key] = value
    return [LegacyScope(name=name, values=values) for name, values in scopes.items()]


def _state_timestamp(path: Path) -> str | None:
    if not path.exists():
        return None
    ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _modern_config_info(path: Path) -> tuple[bool, bool, list[str]]:
    has_local81 = False
    version_ok = False
    in_local81 = False
    scopes: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if stripped == "[local81]":
            has_local81 = True
            in_local81 = True
            continue
        scope_match = _MODERN_SCOPE_RE.match(stripped)
        if scope_match:
            scopes.append(scope_match.group(1))
            in_local81 = False
            continue
        if _SECTION_RE.match(stripped):
            in_local81 = False
            continue
        if in_local81 and "=" in stripped:
            key, value = stripped.split("=", 1)
            if key.strip() == "version" and value.strip() == "0.1":
                version_ok = True
    return has_local81, version_ok, scopes


def _upsert_modern_project(path: Path, project: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_local81 = False
    wrote_project = False
    saw_local81 = False
    for line in lines:
        stripped = line.strip()
        if stripped == "[local81]":
            in_local81 = True
            saw_local81 = True
            wrote_project = False
            out.append("[local81]")
            continue
        if in_local81 and _SECTION_RE.match(stripped):
            if not wrote_project:
                out.append(f"project = {project}")
                wrote_project = True
            in_local81 = False
        if in_local81 and "=" in line:
            key, _value = line.split("=", 1)
            if key.strip() == "project":
                if not wrote_project:
                    out.append(f"project = {project}")
                    wrote_project = True
                continue
        out.append(line)
    if saw_local81 and in_local81 and not wrote_project:
        out.append(f"project = {project}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _write_initial_scope_state(state_dir: Path, scope_name: str) -> None:
    last_path = Path("state") / f"{scope_name}.last"
    state = {
        "schema": "local81.state.v0.1",
        "scope": scope_name,
        "last_success": _state_timestamp(last_path),
        "last_plan_id": None,
        "last_run_id": None,
        "files_last_deployed_count": 0,
    }
    (state_dir / f"{scope_name}.json").write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    (state_dir / f"{scope_name}.json").chmod(0o600)


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def run_init(*, import_path: str | None = None, force: bool = False, project: str | None = None) -> int:
    local81_dir = Path('.local81')
    config_path = local81_dir / 'config.ini'
    state_dir = local81_dir / 'state'
    plans_dir = local81_dir / 'plans'
    runs_dir = local81_dir / 'runs'
    logs_dir = local81_dir / 'logs'

    if config_path.exists() and not force:
        _err(f"{config_path} already exists; rerun with --force")
        return 1

    legacy = Path(import_path) if import_path else Path('./settings.cfg')
    if import_path is None and not legacy.exists():
        _err('no legacy config provided and ./settings.cfg not found')
        return 1
    if not legacy.exists():
        _err(f'legacy config not found: {legacy}')
        return 1
    has_local81, version_ok, modern_scopes = _modern_config_info(legacy)
    if has_local81 or modern_scopes:
        if not has_local81:
            _err("modern config import failed: missing [local81] section")
            return 1
        if not version_ok:
            _err("modern config import failed: [local81] version must be 0.1")
            return 1
        if not modern_scopes:
            _err('modern config import failed: missing [scope "NAME"] sections')
            return 1
        for path in (local81_dir, state_dir, plans_dir, runs_dir, logs_dir):
            _mkdir_private(path)
        shutil.copyfile(legacy, config_path)
        config_path.chmod(0o600)
        if project:
            _upsert_modern_project(config_path, project)
        for scope_name in modern_scopes:
            _write_initial_scope_state(state_dir, scope_name)
        print("Local-81 project initialized")
        print("========================")
        print(f"Imported {len(modern_scopes)} scope(s) from {legacy}.")
        print(f"Config written to: {config_path}")
        print("Next good steps: run 'local81 doctor', then 'local81 plan --summary'.")
        return 0

    scopes = _parse_legacy(legacy)
    if not scopes:
        _err(f'no scopes found in legacy config: {legacy}')
        return 1

    for path in (local81_dir, state_dir, plans_dir, runs_dir, logs_dir):
        _mkdir_private(path)

    valid_scopes: list[LegacyScope] = []
    for scope in scopes:
        values = scope.values
        required = ['source_dir', 'target_dir', 'servers']
        if any(key not in values for key in required):
            _err(f'legacy section [{scope.name}] missing required keys (source_dir, target_dir, servers)')
            return 1
        normalized = {
            'source_dir': values['source_dir'].strip(),
            'target_dir': values['target_dir'].strip(),
            'servers': _normalize_servers(values['servers']),
        }
        if 'rsync_opts' in values:
            normalized['rsync_opts'] = values['rsync_opts'].strip()
        if 'backup' in values:
            parsed = _parse_bool(values['backup'])
            if parsed is None:
                _warn(f"legacy section [{scope.name}] has invalid backup value '{values['backup']}'; expected true/false, ignoring")
            else:
                normalized['backup'] = 'true' if parsed else 'false'
        if 'backup_suffix' in values:
            normalized['backup_suffix'] = values['backup_suffix'].strip()
        valid_scopes.append(LegacyScope(scope.name, normalized))

    default_scope = valid_scopes[0].name
    project_name = project or Path.cwd().name

    config_lines = [
        '[local81]',
        'version = 0.1',
        f'project = {project_name}',
        f'default_scope = {default_scope}',
        'state_dir = .local81/state',
        'plans_dir = .local81/plans',
        'runs_dir = .local81/runs',
        'logs_dir = .local81/logs',
        'lock_file = .local81/local81.lock',
        'require_plan_for_deploy = true',
        'fail_fast = true',
        'max_parallel = 4',
        'shell = /usr/bin/bash',
        '',
        '[tools]',
        'ssh = /usr/bin/ssh',
        'rsync = /usr/bin/rsync',
        'find = /usr/bin/find',
        '',
        '[defaults]',
        'rsync_opts = -az',
        'backup = true',
        'backup_suffix = .bkp',
        'remote_mkdir = true',
        'dry_run_default = false',
        'log_hosts = ',
        'log_dest_dir = .local81/pulled-logs',
        'jboss_log_path = ',
        'apache_log_path = ',
        '',
        '[routing]',
        'env_from_filename_prefix = s:sys,q:qa,p:production',
        'env_from_server_name_char_at = 4',
        'env_from_server_name_char_map = s:sys,q:qa,p:production',
        '',
        '[access]',
        'allowed_users = ',
        'allowed_groups = ',
        'denied_users = ',
        'deny_root = false',
        'allow_remote_cmd = false',
        '',
    ]

    for scope in valid_scopes:
        config_lines.append(f'[scope "{scope.name}"]')
        config_lines.append('enabled = true')
        config_lines.append(f"source_dir = {scope.values['source_dir']}")
        config_lines.append(f"target_dir = {scope.values['target_dir']}")
        config_lines.append(f"servers = {scope.values['servers']}")
        config_lines.append('discovery = mtime_since_last_success')
        if 'rsync_opts' in scope.values:
            config_lines.append(f"rsync_opts = {scope.values['rsync_opts']}")
        if 'backup' in scope.values:
            config_lines.append(f"backup = {scope.values['backup']}")
        if 'backup_suffix' in scope.values:
            config_lines.append(f"backup_suffix = {scope.values['backup_suffix']}")
        config_lines.append('')

        _write_initial_scope_state(state_dir, scope.name)

    config_path.write_text('\n'.join(config_lines) + '\n', encoding='utf-8')
    config_path.chmod(0o600)
    print('Local-81 project initialized')
    print('========================')
    print(f"Imported {len(valid_scopes)} scope(s) from {legacy}.")
    print(f"Default scope: {default_scope}")
    print(f"Config written to: {config_path}")
    print("Next good steps: run 'local81 doctor', then 'local81 plan --summary'.")
    return 0
