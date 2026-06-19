"""Fleet onboarding primitives: key lifecycle, host probes, key distribution.

These back ``local81 keys`` and ``local81 onboard``. They are pure-ish helpers
that route every subprocess through :func:`runner.run_local`, so tests can
intercept without touching a real host, and network probes are read-only.

Mutating actions (generating a key, ``ssh-copy-id``) follow the project
convention: they are *planned* unless ``execute=True`` is passed.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

from .runner import run_local
from .ssh import SshOptions

DEFAULT_KEY_PATH = "~/.ssh/id_ed25519"


def parse_host_list(hosts: str | None = None, hosts_file: str | None = None) -> list[str]:
    """Build a deduped, order-preserving host list from a CSV and/or a file.

    The file accepts one host per line; ``#`` comments and blanks are skipped,
    and a whitespace/tab ``ip server alias`` row contributes its first token.
    """
    collected: list[str] = []
    if hosts:
        collected += [h.strip() for h in hosts.split(",") if h.strip()]
    if hosts_file:
        for line in Path(hosts_file).expanduser().read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith(";"):
                continue
            collected.append(stripped.split()[0])
    seen: set[str] = set()
    ordered: list[str] = []
    for host in collected:
        if host not in seen:
            seen.add(host)
            ordered.append(host)
    return ordered


def key_paths(path: str | None, ssh_opts: SshOptions | None = None) -> tuple[Path, Path]:
    """Resolve (private, public) key paths from an arg, [ssh] config, or default."""
    raw = path or (ssh_opts.identity_file if ssh_opts else None) or DEFAULT_KEY_PATH
    priv = Path(raw).expanduser()
    return priv, priv.with_name(priv.name + ".pub")


@dataclass(slots=True)
class KeyStatus:
    private: str
    public: str
    exists: bool
    private_mode: str | None = None
    perms_ok: bool = False
    created: bool = False


def inspect_key(priv: Path, pub: Path) -> KeyStatus:
    exists = priv.is_file()
    mode = None
    perms_ok = False
    if exists:
        mode = oct(priv.stat().st_mode & 0o777)
        perms_ok = (priv.stat().st_mode & 0o077) == 0  # no group/other access
    return KeyStatus(private=str(priv), public=str(pub), exists=exists,
                     private_mode=mode, perms_ok=perms_ok)


def ensure_key(priv: Path, pub: Path, *, key_type: str = "ed25519",
               comment: str | None = None, execute: bool = False) -> KeyStatus:
    """Generate a keypair if absent (only when execute), and tighten perms.

    Idempotent: an existing private key is never overwritten. Perms are
    corrected to ``0600`` (private) / ``0644`` (public) whenever the key is
    present, so ``ensure`` doubles as a perm fixer.
    """
    status = inspect_key(priv, pub)
    created = False
    if not status.exists and execute:
        priv.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(priv.parent, 0o700)
        except OSError:
            pass
        argv = ["ssh-keygen", "-t", key_type, "-N", "", "-f", str(priv), "-q",
                "-C", comment or f"local81@{socket.gethostname()}"]
        result = run_local(argv)
        if result.returncode != 0:
            return status  # generation failed; caller reports rc via re-inspect
        created = True
    if (status.exists or created):
        try:
            priv.chmod(0o600)
            if pub.is_file():
                pub.chmod(0o644)
        except OSError:
            pass
    status = inspect_key(priv, pub)
    status.created = created
    return status


def tcp_open(host: str, port: int = 22, *, timeout: float = 3.0) -> bool:
    """Read-only reachability check: can we open TCP to the ssh port?"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass(slots=True)
class HostProbe:
    host: str
    target: str
    tcp_open: bool = False
    key_authed: bool = False
    uptime: str = ""
    python3: str = ""
    rsync: str = ""
    sha256sum: bool = False
    rc: int | None = None
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "target": self.target,
            "tcp_open": self.tcp_open,
            "key_authed": self.key_authed,
            "uptime": self.uptime,
            "python3": self.python3,
            "rsync": self.rsync,
            "sha256sum": self.sha256sum,
            "rc": self.rc,
            "error": self.error,
        }

    @property
    def ready(self) -> bool:
        return self.key_authed and bool(self.python3) and bool(self.rsync) and self.sha256sum


# A single read-only remote line that the uptime sweep parses field by field.
_PROBE_REMOTE = (
    "echo UP=$(uptime 2>/dev/null); "
    "echo PY=$(python3 -V 2>&1); "
    "echo RS=$(rsync --version 2>/dev/null | head -1); "
    "echo SH=$(command -v sha256sum >/dev/null 2>&1 && echo yes || echo no)"
)


def probe_host(host: str, ssh_opts: SshOptions | None = None, *, connect_timeout: int = 5) -> HostProbe:
    """Read-only readiness probe: TCP reach + key-auth uptime/tooling sweep.

    Uses ``BatchMode=yes`` so a host that is reachable but not yet key-authed
    fails fast (no password prompt) rather than hanging — that distinguishes
    "needs onboarding" from "ready".
    """
    opts = ssh_opts or SshOptions()
    target = opts.host_for(host)
    probe = HostProbe(host=host, target=target)
    probe.tcp_open = tcp_open(host, opts.port or 22, timeout=min(connect_timeout, 3))
    args = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}", *opts.ssh_args()]
    result = run_local(["ssh", *args, target, _PROBE_REMOTE], timeout_seconds=connect_timeout + 10)
    probe.rc = result.returncode
    if result.returncode != 0:
        probe.error = (result.stderr or "ssh probe failed").strip().splitlines()[-1] if result.stderr else "ssh probe failed"
        return probe
    probe.key_authed = True
    for line in result.stdout.splitlines():
        if line.startswith("UP="):
            probe.uptime = line[3:].strip()
        elif line.startswith("PY="):
            probe.python3 = line[3:].strip()
        elif line.startswith("RS="):
            probe.rsync = line[3:].strip()
        elif line.startswith("SH="):
            probe.sha256sum = line[3:].strip() == "yes"
    return probe


def copy_id_argv(host: str, pub: Path, ssh_opts: SshOptions | None = None) -> list[str]:
    """Build the ``ssh-copy-id`` argv that authorizes our key on a host."""
    opts = ssh_opts or SshOptions()
    argv = ["ssh-copy-id", "-i", str(pub)]
    if opts.port:
        argv += ["-p", str(opts.port)]
    if opts.strict_host_key_checking:
        argv += ["-o", f"StrictHostKeyChecking={opts.strict_host_key_checking}"]
    if opts.connect_timeout:
        argv += ["-o", f"ConnectTimeout={opts.connect_timeout}"]
    argv.append(opts.host_for(host))
    return argv


@dataclass(slots=True)
class CopyResult:
    host: str
    argv: list[str]
    executed: bool = False
    rc: int | None = None
    error: str = ""


def copy_key(host: str, pub: Path, ssh_opts: SshOptions | None = None, *, execute: bool = False) -> CopyResult:
    argv = copy_id_argv(host, pub, ssh_opts)
    if not execute:
        return CopyResult(host=host, argv=argv, executed=False)
    result = run_local(argv)
    return CopyResult(host=host, argv=argv, executed=True, rc=result.returncode,
                      error=(result.stderr or "").strip())


@dataclass(slots=True)
class OnboardReport:
    created_at: str
    key: dict
    execute: bool
    hosts: list[dict] = field(default_factory=list)
    copies: list[dict] = field(default_factory=list)
