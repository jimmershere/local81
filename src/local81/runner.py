from __future__ import annotations

import shlex
import subprocess
from typing import Mapping

from .models import CommandResult


class RunnerError(RuntimeError):
    """Raised when a command execution fails."""


def _normalize_command(command: str | list[str]) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command)
    return [str(part) for part in command]


def run_local(
    command: str | list[str],
    *,
    cwd: str | None = None,
    timeout_seconds: int | None = None,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
    check: bool = False,
) -> CommandResult:
    argv = _normalize_command(command)
    if dry_run:
        return CommandResult(command=argv, returncode=0, stdout="", stderr="", dry_run=True)

    completed = subprocess.run(
        argv,
        cwd=cwd,
        timeout=timeout_seconds,
        env=dict(env) if env else None,
        capture_output=True,
        text=True,
        check=False,
    )
    result = CommandResult(
        command=argv,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        timed_out=False,
        dry_run=False,
    )
    if check and result.returncode != 0:
        raise RunnerError(f"command failed rc={result.returncode}: {' '.join(argv)}")
    return result


def run_remote(
    host: str,
    remote_command: str,
    *,
    ssh_bin: str = "ssh",
    ssh_args: list[str] | None = None,
    cwd: str | None = None,
    timeout_seconds: int | None = None,
    dry_run: bool = False,
    check: bool = False,
) -> CommandResult:
    remote = remote_command if not cwd else f"cd {shlex.quote(cwd)} && {remote_command}"
    return run_local(
        [ssh_bin, *(ssh_args or []), host, remote],
        timeout_seconds=timeout_seconds,
        dry_run=dry_run,
        check=check,
    )
