"""``local81 onboard`` — bring a fleet of hosts to a deployable state.

One operator-facing flow over the :mod:`local81.onboarding` primitives:

1. ensure a local SSH keypair exists,
2. authorize it on each host via ``ssh-copy-id`` (the bootstrap that turns a
   password into key auth),
3. run a read-only **uptime + tooling sweep** against every host, and
4. write a per-customer readiness report to ``.local81/reports/``.

Without ``--execute`` it is a plan: it never generates a key or touches a
remote ``authorized_keys``; it still runs the read-only sweep so already-keyed
hosts (and TCP reachability) are reported honestly.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from local81.onboarding import (
    copy_key,
    ensure_key,
    key_paths,
    parse_host_list,
    probe_host,
)
from local81.ssh import SshOptions


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ssh_options(profile: str | None) -> SshOptions:
    from local81.config import load_config

    try:
        return load_config(profile=profile).ssh_options()
    except FileNotFoundError:
        return SshOptions()


def _write_report(payload: dict) -> Path:
    reports = Path(".local81") / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    try:
        reports.chmod(0o700)
    except OSError:
        pass
    path = reports / f"onboard-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
    return path


def run_onboard(*, hosts: str | None = None, hosts_file: str | None = None,
                path: str | None = None, key_type: str = "ed25519",
                connect_timeout: int = 5, execute: bool = False,
                profile: str | None = None) -> int:
    host_list = parse_host_list(hosts, hosts_file)
    if not host_list:
        print("Local-81 onboard: no hosts given (use --hosts a,b,c or --hosts-file PATH).")
        return 1

    ssh_opts = _ssh_options(profile)
    priv, pub = key_paths(path, ssh_opts)

    print("Local-81 onboard")
    print("================")
    print(f"Hosts: {', '.join(host_list)}")
    print(f"Key:   {priv}")
    print(f"Mode:  {'execute' if execute else 'plan (read-only; re-run with --execute to apply)'}\n")

    # 1) Local key.
    key_status = ensure_key(priv, pub, key_type=key_type, execute=execute)
    if key_status.exists:
        print(f"[{'ok' if key_status.perms_ok else 'warn'}] key {key_status.private} "
              f"({'generated' if key_status.created else 'present'}, mode {key_status.private_mode})")
    elif execute:
        print(f"[fail] could not generate key at {priv}")
        return 1
    else:
        print(f"[plan] would generate {key_type} keypair at {priv}")

    # 2) Probe first so we only copy to hosts that aren't already keyed.
    print("\nReadiness sweep (read-only):")
    probes = [probe_host(host, ssh_opts, connect_timeout=connect_timeout) for host in host_list]
    for p in probes:
        if p.ready:
            print(f"[ok]   {p.host}: ready — {p.uptime or 'up'} | {p.python3} | {p.rsync}")
        elif p.key_authed:
            missing = [n for n, ok in (("python3", p.python3), ("rsync", p.rsync), ("sha256sum", p.sha256sum)) if not ok]
            print(f"[warn] {p.host}: key OK but missing {', '.join(missing)}")
        elif p.tcp_open:
            print(f"[warn] {p.host}: reachable (tcp/22) but not key-authed — needs onboarding")
        else:
            print(f"[fail] {p.host}: unreachable ({p.error or 'no tcp/22'})")

    # 3) Distribute the key to hosts that need it.
    copies = []
    needs_key = [p.host for p in probes if not p.key_authed]
    if needs_key:
        print(f"\nKey distribution ({'execute' if execute else 'plan'}):")
        for host in needs_key:
            result = copy_key(host, pub, ssh_opts, execute=execute and key_status.exists)
            copies.append({"host": host, "argv": result.argv, "executed": result.executed,
                           "rc": result.rc, "error": result.error})
            if not result.executed:
                print(f"[plan] {host}: would run: {' '.join(result.argv)}")
            elif result.rc == 0:
                print(f"[ok]   {host}: key installed")
            else:
                print(f"[fail] {host}: ssh-copy-id rc={result.rc} {result.error}".rstrip())

    # 4) Persist a per-customer readiness report.
    payload = {
        "schema": "local81.onboard.v0.1",
        "created_at": _now_iso(),
        "execute": execute,
        "key": {"private": key_status.private, "public": key_status.public,
                "exists": key_status.exists, "mode": key_status.private_mode},
        "hosts": [p.to_dict() for p in probes],
        "copies": copies,
    }
    report_path = _write_report(payload)

    ready = sum(1 for p in probes if p.ready)
    print(f"\nReadiness: {ready}/{len(probes)} host(s) ready. Report: {report_path}")
    if not execute and needs_key:
        print("Re-run with --execute to generate/copy keys and complete onboarding.")
    # A plan run is informational (rc 0). An execute run fails if any host
    # is still not ready after the attempt.
    if execute:
        return 0 if ready == len(probes) else 1
    return 0
