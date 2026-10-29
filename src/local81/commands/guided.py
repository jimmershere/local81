from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, TextIO

import yaml


@dataclass(slots=True)
class GuidedAnswers:
    project: str = ""
    scope_name: str = "main"
    source_dir: str = ""
    target_dir: str = ""
    remote_mkdir: bool = True
    servers: str = ""
    rsync_opts: str = "-az"
    fail_fast: bool = True
    backup: bool = True
    backup_suffix: str = ".bkp"
    max_parallel: int = 4
    log_hosts: str = ""
    jboss_log_path: str = ""
    apache_log_path: str = ""


def _section(title: str, subtitle: str | None = None, *, out: TextIO = sys.stdout) -> None:
    out.write(f"\n== {title} ==\n")
    if subtitle:
        out.write(f"{subtitle}\n")


def _prompt(message: str, default: str = "", *, inp: IO[str] = sys.stdin, out: TextIO = sys.stdout) -> str:
    if default:
        out.write(f"{message} [{default}]: ")
    else:
        out.write(f"{message}: ")
    out.flush()
    value = inp.readline()
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _prompt_bool(message: str, default: bool = True, *, inp: IO[str] = sys.stdin, out: TextIO = sys.stdout) -> bool:
    hint = "Y/n" if default else "y/N"
    out.write(f"{message} [{hint}]: ")
    out.flush()
    value = inp.readline()
    if value is None:
        return default
    value = value.strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "true", "1"}


def _prompt_int(message: str, default: int, *, inp: IO[str] = sys.stdin, out: TextIO = sys.stdout) -> int:
    out.write(f"{message} [{default}]: ")
    out.flush()
    value = inp.readline()
    if value is None:
        return default
    value = value.strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        out.write(f"That does not look like a number, so I'll keep the default of {default}.\n")
        return default
    return parsed if parsed > 0 else default


def _normalize_servers(raw: str) -> str:
    tokens = raw.replace(",", " ").split()
    seen: set[str] = set()
    deduped: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            deduped.append(token)
    return ",".join(deduped)


def _build_config_payload(answers: GuidedAnswers) -> dict[str, Any]:
    return {
        "local81": {
            "version": "0.1",
            "project": answers.project,
            "default_scope": answers.scope_name,
            "state_dir": ".local81/state",
            "plans_dir": ".local81/plans",
            "runs_dir": ".local81/runs",
            "logs_dir": ".local81/logs",
            "lock_file": ".local81/local81.lock",
            "require_plan_for_deploy": True,
            "fail_fast": answers.fail_fast,
            "max_parallel": answers.max_parallel,
            "shell": "/usr/bin/bash",
        },
        "tools": {
            "ssh": "/usr/bin/ssh",
            "rsync": "/usr/bin/rsync",
            "find": "/usr/bin/find",
        },
        "defaults": {
            "rsync_opts": answers.rsync_opts,
            "backup": answers.backup,
            "backup_suffix": answers.backup_suffix,
            "remote_mkdir": answers.remote_mkdir,
            "dry_run_default": False,
            "log_hosts": answers.log_hosts,
            "log_dest_dir": ".local81/pulled-logs",
            "jboss_log_path": answers.jboss_log_path,
            "apache_log_path": answers.apache_log_path,
        },
        "routing": {
            "env_from_filename_prefix": "s:sys,q:qa,p:production",
            "env_from_server_name_char_at": 4,
            "env_from_server_name_char_map": "s:sys,q:qa,p:production",
        },
        "scopes": {
            answers.scope_name: {
                "enabled": True,
                "source_dir": answers.source_dir,
                "target_dir": answers.target_dir,
                "servers": answers.servers.split(",") if answers.servers else [],
                "discovery": "mtime_since_last_success",
                "rsync_opts": answers.rsync_opts,
                "backup": answers.backup,
                "backup_suffix": answers.backup_suffix,
            }
        },
    }


def run_guided_interview(*, inp: IO[str] = sys.stdin, out: TextIO = sys.stdout) -> GuidedAnswers | None:
    answers = GuidedAnswers()
    cwd_name = Path.cwd().name

    out.write("\nWelcome to Local-81 guided setup.\n")
    out.write("I'll walk with you through one clean deploy path, then show you the config before anything is written.\n")

    _section("Project identity", "Let's name the project and the first deploy scope.", out=out)
    answers.project = _prompt("Project name", cwd_name, inp=inp, out=out)
    answers.scope_name = _prompt("Default scope name", "main", inp=inp, out=out)

    _section("Deployment source", "Point Local-81 at the local files you actually want to ship.", out=out)
    answers.source_dir = _prompt("Local source directory", inp=inp, out=out)
    if not answers.source_dir:
        out.write("I still need a source directory to build a usable config.\n")
        answers.source_dir = _prompt("Please enter the local source directory", inp=inp, out=out)
        if not answers.source_dir:
            out.write("Stopping here so we do not write a broken config.\n")
            return None
    source_path = Path(answers.source_dir)
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path
    if source_path.exists():
        out.write(f"Using source directory: {source_path}\n")
    else:
        keep_going = _prompt_bool(
            f"I can't find {answers.source_dir} yet. Keep it as a placeholder for now?",
            True,
            inp=inp,
            out=out,
        )
        if not keep_going:
            answers.source_dir = _prompt("Okay, what local directory should Local-81 deploy from instead?", inp=inp, out=out)
            if not answers.source_dir:
                out.write("Stopping here so we do not write a broken config.\n")
                return None

    _section("Deployment target", "Now the remote side.", out=out)
    answers.target_dir = _prompt("Remote target directory", inp=inp, out=out)
    if not answers.target_dir:
        out.write("I still need a target directory to finish the config.\n")
        answers.target_dir = _prompt("Please enter the remote target directory", inp=inp, out=out)
        if not answers.target_dir:
            out.write("Stopping here so we do not write a broken config.\n")
            return None
    answers.remote_mkdir = _prompt_bool("Create missing remote directories automatically?", True, inp=inp, out=out)

    _section("Targets", "Tell me where this scope should go.", out=out)
    raw_servers = _prompt("Target host(s)", inp=inp, out=out)
    if not raw_servers:
        out.write("I need at least one host before we can move on.\n")
        raw_servers = _prompt("Please enter one or more target hosts", inp=inp, out=out)
        if not raw_servers:
            out.write("Stopping here so we do not write a broken config.\n")
            return None
    answers.servers = _normalize_servers(raw_servers)
    out.write(f"Normalized target hosts: {answers.servers}\n")

    _section("Transfer and safety", "We'll set the defaults you are most likely to care about on day one.", out=out)
    answers.rsync_opts = _prompt("Rsync options", "-az", inp=inp, out=out)
    answers.fail_fast = _prompt_bool("Stop on the first failure?", True, inp=inp, out=out)
    answers.backup = _prompt_bool("Create backups before overwriting files?", True, inp=inp, out=out)
    if answers.backup:
        answers.backup_suffix = _prompt("Backup suffix", ".bkp", inp=inp, out=out)
    answers.max_parallel = _prompt_int("Maximum parallel workers (1 is a cautious first deploy)", 4, inp=inp, out=out)

    _section("Logs and diagnostics", "Optional now, handy later.", out=out)
    want_logs = _prompt_bool("Set default log hosts now?", False, inp=inp, out=out)
    if want_logs:
        answers.log_hosts = _normalize_servers(_prompt("Log hosts", inp=inp, out=out))
        want_paths = _prompt_bool("Do you already know the common log paths?", False, inp=inp, out=out)
        if want_paths:
            answers.jboss_log_path = _prompt("JBoss log path", inp=inp, out=out)
            answers.apache_log_path = _prompt("Apache log path", inp=inp, out=out)
    else:
        out.write("No problem. We can leave logs for later.\n")

    _section("Review", None, out=out)
    out.write(f"Project: {answers.project}\n")
    out.write(f"Scope: {answers.scope_name}\n")
    out.write(f"Source -> target: {answers.source_dir} -> {answers.target_dir}\n")
    out.write(f"Hosts: {answers.servers}\n")
    out.write(f"Rsync: {answers.rsync_opts}\n")
    out.write(f"Backups: {'on' if answers.backup else 'off'}\n")
    out.write(f"Max parallel: {answers.max_parallel}\n")
    return answers


def generate_config(answers: GuidedAnswers) -> str:
    payload = _build_config_payload(answers)
    defaults = payload["defaults"]
    local81 = payload["local81"]
    routing = payload["routing"]
    scope = payload["scopes"][answers.scope_name]
    lines = [
        "[local81]",
        f"version = {local81['version']}",
        f"project = {local81['project']}",
        f"default_scope = {local81['default_scope']}",
        f"state_dir = {local81['state_dir']}",
        f"plans_dir = {local81['plans_dir']}",
        f"runs_dir = {local81['runs_dir']}",
        f"logs_dir = {local81['logs_dir']}",
        f"lock_file = {local81['lock_file']}",
        f"require_plan_for_deploy = {'true' if local81['require_plan_for_deploy'] else 'false'}",
        f"fail_fast = {'true' if local81['fail_fast'] else 'false'}",
        f"max_parallel = {local81['max_parallel']}",
        f"shell = {local81['shell']}",
        "",
        "[tools]",
        "ssh = /usr/bin/ssh",
        "rsync = /usr/bin/rsync",
        "find = /usr/bin/find",
        "",
        "[defaults]",
        f"rsync_opts = {defaults['rsync_opts']}",
        f"backup = {'true' if defaults['backup'] else 'false'}",
        f"backup_suffix = {defaults['backup_suffix']}",
        f"remote_mkdir = {'true' if defaults['remote_mkdir'] else 'false'}",
        "dry_run_default = false",
        f"log_hosts = {defaults['log_hosts']}",
        f"log_dest_dir = {defaults['log_dest_dir']}",
        f"jboss_log_path = {defaults['jboss_log_path']}",
        f"apache_log_path = {defaults['apache_log_path']}",
        "",
        "[routing]",
        f"env_from_filename_prefix = {routing['env_from_filename_prefix']}",
        f"env_from_server_name_char_at = {routing['env_from_server_name_char_at']}",
        f"env_from_server_name_char_map = {routing['env_from_server_name_char_map']}",
        "",
        f'[scope "{answers.scope_name}"]',
        "enabled = true",
        f"source_dir = {scope['source_dir']}",
        f"target_dir = {scope['target_dir']}",
        f"servers = {','.join(scope['servers'])}",
        f"discovery = {scope['discovery']}",
        f"rsync_opts = {scope['rsync_opts']}",
        f"backup = {'true' if scope['backup'] else 'false'}",
        f"backup_suffix = {scope['backup_suffix']}",
        "",
    ]
    return "\n".join(lines)


def generate_config_yaml(answers: GuidedAnswers) -> str:
    return yaml.safe_dump(_build_config_payload(answers), sort_keys=False)


def preview_config(config_text: str, *, out: TextIO = sys.stdout) -> None:
    _section("Config preview", "This is the INI form Local-81 uses today. A YAML mirror will be written too.", out=out)
    for line in config_text.splitlines():
        out.write(f"  {line}\n")
    out.write("\n")


def _write_project_files(answers: GuidedAnswers, config_text: str, config_yaml: str, *, out: TextIO = sys.stdout) -> int:
    local81_dir = Path(".local81")
    config_ini_path = local81_dir / "config.ini"
    config_yaml_path = local81_dir / "config.yaml"
    local81_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("state", "plans", "runs", "logs"):
        (local81_dir / sub).mkdir(parents=True, exist_ok=True)
    config_ini_path.write_text(config_text, encoding="utf-8")
    config_yaml_path.write_text(config_yaml, encoding="utf-8")

    state = {
        "schema": "local81.state.v0.1",
        "scope": answers.scope_name,
        "last_success": None,
        "last_plan_id": None,
        "last_run_id": None,
        "files_last_deployed_count": 0,
    }
    state_file = local81_dir / "state" / f"{answers.scope_name}.json"
    state_file.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")

    out.write(f"Wrote {config_ini_path}.\n")
    out.write(f"Wrote {config_yaml_path}.\n")
    out.write(f"Local-81 is ready for project '{answers.project}' with default scope '{answers.scope_name}'.\n")
    out.write("Next good steps: run 'local81 doctor', then 'local81 plan --summary'.\n")
    return 0


def run_guided(*, force: bool = False, inp: IO[str] = sys.stdin, out: TextIO = sys.stdout) -> int:
    local81_dir = Path(".local81")
    config_ini_path = local81_dir / "config.ini"
    config_yaml_path = local81_dir / "config.yaml"
    if (config_ini_path.exists() or config_yaml_path.exists()) and not force:
        out.write(f"local81: {config_ini_path} or {config_yaml_path} already exists; rerun with --force\n")
        return 1

    while True:
        answers = run_guided_interview(inp=inp, out=out)
        if answers is None:
            out.write("Setup cancelled.\n")
            return 1

        config_text = generate_config(answers)
        config_yaml = generate_config_yaml(answers)
        preview_config(config_text, out=out)
        choice = _prompt("Write this config now? (write / edit / cancel)", "write", inp=inp, out=out).strip().lower()
        if choice == "cancel":
            out.write("Cancelled. No files written.\n")
            return 1
        if choice == "edit":
            out.write("Okay, let's run through it once more.\n")
            continue
        return _write_project_files(answers, config_text, config_yaml, out=out)
