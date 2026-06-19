"""``local81 keys`` — SSH key lifecycle for the control node.

Subcommands:

* ``ensure`` — generate an ed25519 keypair if absent (``--execute``) and fix
  perms to 0600/0644. Idempotent; never overwrites an existing key.
* ``check``  — report key presence, perms, and whether the agent holds it.
* ``copy``   — authorize the key on a list of hosts via ``ssh-copy-id``. A
  remote mutation, so it is *planned* unless ``--execute`` is given.
"""

from __future__ import annotations

from pathlib import Path

from local81.onboarding import (
    copy_key,
    ensure_key,
    inspect_key,
    key_paths,
    parse_host_list,
)
from local81.runner import run_local
from local81.ssh import SshOptions


def _ssh_options(profile: str | None) -> SshOptions:
    """Pull [ssh] options from config if present; key ops work without config."""
    from local81.config import load_config

    try:
        return load_config(profile=profile).ssh_options()
    except FileNotFoundError:
        return SshOptions()


def run_keys(action: str, *, path: str | None = None, key_type: str = "ed25519",
             comment: str | None = None, hosts: str | None = None,
             hosts_file: str | None = None, execute: bool = False,
             profile: str | None = None) -> int:
    ssh_opts = _ssh_options(profile)
    priv, pub = key_paths(path, ssh_opts)

    if action == "ensure":
        before = inspect_key(priv, pub)
        if not before.exists and not execute:
            print(f"Local-81 keys: would generate {key_type} keypair at {priv} (re-run with --execute).")
            return 0
        status = ensure_key(priv, pub, key_type=key_type, comment=comment, execute=execute)
        if not status.exists:
            print(f"[fail] key generation failed for {priv}")
            return 1
        verb = "generated" if status.created else "present"
        print(f"Local-81 keys: {verb} {status.private} (mode {status.private_mode}); public {status.public}")
        if not status.perms_ok:
            print(f"[warn] {status.private} is group/other-accessible even after chmod; check the filesystem")
        return 0

    if action == "check":
        status = inspect_key(priv, pub)
        if not status.exists:
            print(f"[warn] no private key at {status.private} (run: local81 keys ensure --execute)")
            return 1
        tag = "ok" if status.perms_ok else "warn"
        print(f"[{tag}] private key {status.private} mode={status.private_mode} "
              f"({'owner-only' if status.perms_ok else 'TOO OPEN'})")
        print(f"[{'ok' if Path(status.public).is_file() else 'warn'}] public key {status.public}")
        agent = run_local(["ssh-add", "-l"])
        if agent.returncode == 0 and agent.stdout.strip():
            print("[ok] ssh-agent holds key(s):")
            for line in agent.stdout.strip().splitlines():
                print(f"      {line}")
        else:
            print("[info] ssh-agent holds no keys (or no agent running)")
        return 0 if status.perms_ok else 1

    if action == "copy":
        host_list = parse_host_list(hosts, hosts_file)
        if not host_list:
            print("Local-81 keys copy: no hosts given (use --hosts a,b or --hosts-file PATH).")
            return 1
        status = inspect_key(priv, pub)
        if not status.exists:
            print(f"[fail] no key at {priv}; run: local81 keys ensure --execute")
            return 1
        rc = 0
        for host in host_list:
            result = copy_key(host, pub, ssh_opts, execute=execute)
            shown = " ".join(result.argv)
            if not result.executed:
                print(f"[plan] {host}: would run: {shown}")
            elif result.rc == 0:
                print(f"[ok]   {host}: key installed")
            else:
                rc = 1
                print(f"[fail] {host}: ssh-copy-id rc={result.rc} {result.error}".rstrip())
        if not execute:
            print(f"\nPlanned key distribution to {len(host_list)} host(s). Re-run with --execute to apply.")
        return rc

    print(f"Local-81 keys: unknown action {action!r}")
    return 2
