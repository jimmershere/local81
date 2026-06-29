"""``local81 m2`` — classify M2 hosts and emit their read-only stack-discovery plan.

Two read-only sub-commands over :mod:`local81.recipes.m2`:

* ``m2 classify`` — print the role each host name maps to (m2in->postgres,
  m2or->oracle, m2tr->mq, m2->app). Fail-closed: a non-M2 name is an error.
* ``m2 plan`` — for each host, render the read-only discovery checks (where the
  JBoss/Oracle/PostgreSQL/MQ stack lives). With ``--out`` it also writes a
  validated ``local81.recipes.v1`` fleet catalog, a per-host discovery shell
  script, and a plan TSV.

Nothing here contacts a host or changes state; it only classifies names and
renders the commands an operator (or ``deploy``/``doctor``/``pull-logs``) would
run next.
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from local81.onboarding import parse_host_list
from local81.recipes import m2


def _resolve_hosts(hosts: str | None, hosts_file: str | None) -> list[str]:
    return parse_host_list(hosts, hosts_file)


def _classify(hosts: list[str]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (classified, errors) as lists of (host, role) / (host, message)."""
    classified: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    for host in hosts:
        try:
            classified.append((host, m2.classify_host(host)))
        except m2.M2Error as exc:
            errors.append((host, str(exc)))
    return classified, errors


def run_m2_classify(*, hosts: str | None, hosts_file: str | None, output_format: str = "text") -> int:
    host_list = _resolve_hosts(hosts, hosts_file)
    if not host_list:
        print("local81 m2: no hosts given (use --hosts a,b,c or --hosts-file PATH).", file=sys.stderr)
        return 2
    classified, errors = _classify(host_list)

    if output_format == "json":
        print(json.dumps({
            "schema": "local81.m2.classify.v0.1",
            "hosts": [{"host": h, "role": r} for h, r in classified],
            "errors": [{"host": h, "error": e} for h, e in errors],
        }, indent=2))
    else:
        for host, role in classified:
            print(f"[ok]   {host} -> {role}")
        for host, err in errors:
            print(f"[skip] {host}: {err}", file=sys.stderr)
    return 0 if not errors else 1


def _write_outputs(out_dir: Path, classified: list[tuple[str, str]], *,
                   fleet_name: str, base_port: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    hosts = [h for h, _r in classified]
    catalog = out_dir / "fleet-m2.yaml"
    catalog.write_text(m2.render_m2_catalog(hosts, name=fleet_name, base_port=base_port), encoding="utf-8")
    written.append(catalog)

    discover_dir = out_dir / "discover"
    discover_dir.mkdir(parents=True, exist_ok=True)
    for host, _role in classified:
        script = discover_dir / f"{host}.sh"
        script.write_text(m2.render_discovery_script(host), encoding="utf-8")
        written.append(script)

    plan_tsv = out_dir / "discovery-plan.tsv"
    rows = ["host\trole\tcheck\tkind\ttarget\tdescription"]
    for host, role in classified:
        for step in m2.discovery_plan(host):
            c = step.check
            rows.append(f"{host}\t{role}\t{c.key}\t{c.kind}\t{c.target}\t{c.description}")
    plan_tsv.write_text("\n".join(rows) + "\n", encoding="utf-8")
    written.append(plan_tsv)
    return written


def run_m2_plan(*, hosts: str | None, hosts_file: str | None, out: str | None = None,
                output_format: str = "text", fleet_name: str = "m2-fleet",
                base_port: int = 2810) -> int:
    host_list = _resolve_hosts(hosts, hosts_file)
    if not host_list:
        print("local81 m2: no hosts given (use --hosts a,b,c or --hosts-file PATH).", file=sys.stderr)
        return 2
    classified, errors = _classify(host_list)
    if not classified:
        print("local81 m2: no M2 hosts to plan (all names lacked an 'm2' marker).", file=sys.stderr)
        for host, err in errors:
            print(f"[skip] {host}: {err}", file=sys.stderr)
        return 1

    if output_format == "json":
        payload = {
            "schema": "local81.m2.plan.v0.1",
            "hosts": [],
            "errors": [{"host": h, "error": e} for h, e in errors],
        }
        for host, role in classified:
            payload["hosts"].append({
                "host": host,
                "role": role,
                "checks": [
                    {"key": s.check.key, "kind": s.check.kind, "target": s.check.target,
                     "description": s.check.description, "argv": s.argv}
                    for s in m2.discovery_plan(host)
                ],
            })
        print(json.dumps(payload, indent=2))
    else:
        for host, role in classified:
            print(f"\n# {host}  (role {role}) — READ-ONLY discovery")
            for step in m2.discovery_plan(host):
                print(f"  [{step.check.key}] {step.check.description}")
                print(f"      $ {' '.join(shlex.quote(a) for a in step.argv)}")
        for host, err in errors:
            print(f"[skip] {host}: {err}", file=sys.stderr)

    if out:
        written = _write_outputs(Path(out), classified, fleet_name=fleet_name, base_port=base_port)
        if output_format != "json":
            print(f"\nWrote {len(written)} file(s) under {out}/:")
            for path in written:
                print(f"  - {path}")
            print("\nNext (read-only first):")
            print(f"  ssh <host> 'bash -s' < {out}/discover/<host>.sh   # run the discovery report")
            print("  local81 doctor --fleet                            # once .ssh/config + config.ini reference these hosts")
            print(f"  local81 ui semaphore-render --catalog {out}/fleet-m2.yaml --db-host <pg>")

    return 0 if not errors else 1


def run_m2(args) -> int:
    if args.m2_command == "classify":
        return run_m2_classify(hosts=args.hosts, hosts_file=args.hosts_file,
                               output_format=args.format)
    if args.m2_command == "plan":
        return run_m2_plan(hosts=args.hosts, hosts_file=args.hosts_file, out=args.out,
                          output_format=args.format, fleet_name=args.fleet_name,
                          base_port=args.base_port)
    print(f"local81 m2: unsupported subcommand: {args.m2_command}", file=sys.stderr)
    return 2
