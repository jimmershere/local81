"""Connector abstraction: a uniform way for facts and ops to reach a target.

A :class:`Connector` is the single interface the fact, op, and resolve layers
depend on. It models pyinfra's connector index: SSH/rsync is *one*
implementation, not the architecture. Three ship in-tree:

* :class:`LocalConnector` — the control node itself (``@local``). subprocess +
  shutil; makes localhost a first-class target so facts/ops are testable
  without SSH fixtures.
* :class:`SshConnector` — a remote host over SSH; ``put``/``get`` use rsync.
* :class:`DockerConnector` — a local container (``@docker/<name>``) via
  ``docker exec`` / ``docker cp``.

The protocol is deliberately tiny: ``run`` (execute), ``put``/``get`` (move a
file or directory), and ``close`` (release any held resource). A new connector
is implementable against this surface alone — facts and ops never import a
concrete class.
"""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable

from .models import CommandResult
from .runner import run_local, run_remote
from .ssh import SshOptions

# Inventory prefixes that select a connector type, pyinfra-style.
_LOCAL_ALIASES = {"@local", "local"}
_DOCKER_PREFIX = "@docker/"


@runtime_checkable
class Connector(Protocol):
    """Reaches a target to execute commands and move files.

    Implementations must not mutate target state as a side effect of ``run``
    itself; idempotency and change decisions live in the ops layer. Facts use
    ``run`` exclusively for read-only probes.
    """

    name: str

    def run(
        self,
        command: str | list[str],
        *,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult: ...

    def put(self, local_path: str, remote_path: str, *, recursive: bool = False) -> CommandResult: ...

    def get(self, remote_path: str, local_path: str, *, recursive: bool = False) -> CommandResult: ...

    def close(self) -> None: ...


def _ok(command: list[str], stdout: str = "") -> CommandResult:
    return CommandResult(command=command, returncode=0, stdout=stdout, stderr="")


def _fail(command: list[str], stderr: str) -> CommandResult:
    return CommandResult(command=command, returncode=1, stdout="", stderr=stderr)


class LocalConnector:
    """Runs commands and copies files on the control node itself (``@local``)."""

    def __init__(self, name: str = "@local") -> None:
        self.name = name

    def run(
        self,
        command: str | list[str],
        *,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        return run_local(command, timeout_seconds=timeout_seconds, env=env)

    def put(self, local_path: str, remote_path: str, *, recursive: bool = False) -> CommandResult:
        return self._copy(local_path, remote_path, recursive=recursive, verb="put")

    def get(self, remote_path: str, local_path: str, *, recursive: bool = False) -> CommandResult:
        return self._copy(remote_path, local_path, recursive=recursive, verb="get")

    @staticmethod
    def _copy(src: str, dest: str, *, recursive: bool, verb: str) -> CommandResult:
        command = ["cp", "-r" if recursive else "", src, dest]
        try:
            src_path = Path(src)
            dest_path = Path(dest)
            if src_path.is_dir() or recursive:
                shutil.copytree(src_path, dest_path, dirs_exist_ok=True)
            else:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dest_path)
            return _ok(command)
        except OSError as exc:
            return _fail(command, f"local {verb} failed: {exc}")

    def close(self) -> None:
        return None


class SshConnector:
    """Runs commands over SSH; transfers files with rsync."""

    def __init__(self, host: str, *, ssh_bin: str = "ssh", rsync_bin: str = "rsync", rsync_opts: str = "-az",
                 ssh_opts: SshOptions | None = None) -> None:
        self.name = host
        self._ssh_opts = ssh_opts or SshOptions()
        # The host as ssh/rsync should address it (user@host when configured).
        self._host = self._ssh_opts.host_for(host)
        self._ssh_bin = ssh_bin
        self._rsync_bin = rsync_bin
        self._rsync_opts = rsync_opts

    def run(
        self,
        command: str | list[str],
        *,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        if isinstance(command, list):
            remote = " ".join(shlex.quote(part) for part in command)
        else:
            remote = command
        return run_remote(
            self._host,
            remote,
            ssh_bin=self._ssh_bin,
            ssh_args=self._ssh_opts.ssh_args(),
            timeout_seconds=timeout_seconds,
        )

    def put(self, local_path: str, remote_path: str, *, recursive: bool = False) -> CommandResult:
        argv = [self._rsync_bin, *shlex.split(self._rsync_opts), *self._ssh_opts.rsync_transport(),
                "--", local_path, f"{self._host}:{remote_path}"]
        return run_local(argv)

    def get(self, remote_path: str, local_path: str, *, recursive: bool = False) -> CommandResult:
        argv = [self._rsync_bin, *shlex.split(self._rsync_opts), *self._ssh_opts.rsync_transport(),
                "--", f"{self._host}:{remote_path}", local_path]
        return run_local(argv)

    def close(self) -> None:
        return None


class DockerConnector:
    """Runs commands and copies files in a local container.

    Inventory form ``@docker/<container>``. ``run`` shells into the container
    with ``docker exec``; ``put``/``get`` use ``docker cp``.
    """

    def __init__(self, container: str, *, docker_bin: str = "docker") -> None:
        self.container = container
        self.name = f"{_DOCKER_PREFIX}{container}"
        self._docker_bin = docker_bin

    def run(
        self,
        command: str | list[str],
        *,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        if isinstance(command, list):
            script = " ".join(shlex.quote(part) for part in command)
        else:
            script = command
        argv = [self._docker_bin, "exec"]
        for key, value in (env or {}).items():
            argv += ["--env", f"{key}={value}"]
        argv += [self.container, "sh", "-c", script]
        return run_local(argv, timeout_seconds=timeout_seconds)

    def put(self, local_path: str, remote_path: str, *, recursive: bool = False) -> CommandResult:
        argv = [self._docker_bin, "cp", local_path, f"{self.container}:{remote_path}"]
        return run_local(argv)

    def get(self, remote_path: str, local_path: str, *, recursive: bool = False) -> CommandResult:
        argv = [self._docker_bin, "cp", f"{self.container}:{remote_path}", local_path]
        return run_local(argv)

    def close(self) -> None:
        return None


def connector_for_target(target: str | None, *, rsync_opts: str = "-az",
                         ssh_opts: SshOptions | None = None) -> Connector:
    """Resolve an inventory target string to a connector.

    * ``None``, ``@local``, ``local`` -> :class:`LocalConnector`
    * ``@docker/<name>``              -> :class:`DockerConnector`
    * anything else                   -> :class:`SshConnector` (the hostname)

    ``ssh_opts`` (from the ``[ssh]`` config section) pins the user, port,
    identity file and host-key policy on the SSH path; it is ignored for the
    local and docker connectors.
    """
    if target is None or target in _LOCAL_ALIASES:
        return LocalConnector()
    if target.startswith(_DOCKER_PREFIX):
        container = target[len(_DOCKER_PREFIX):]
        return DockerConnector(container)
    return SshConnector(target, rsync_opts=rsync_opts, ssh_opts=ssh_opts)
