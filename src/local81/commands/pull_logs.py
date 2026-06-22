from __future__ import annotations

import configparser
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from local81 import log_safety


def _load_settings(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    text = path.read_text(encoding="utf-8")
    parser.read_string("[settings]\n" + text)
    return {key: value.strip() for key, value in parser.items("settings")}


def _iter_hosts(hosts_csv: str) -> list[str]:
    return [host.strip() for host in hosts_csv.split(",") if host.strip()]


def run_pull_logs(*, settings: str = "./settings.cfg", hosts: str | None = None,
                  dest: str | None = None, jboss_path: str | None = None,
                  apache_path: str | None = None, engin_path: str | None = None,
                  smartxfr_path: str | None = None) -> int:
    settings_path = Path(settings)
    settings_data: dict[str, str] = {}
    if settings_path.is_file():
        settings_data = _load_settings(settings_path)

    hosts_csv = hosts or settings_data.get("log_hosts", "")
    dest_value = dest or settings_data.get("log_dest_dir") or ".local81/pulled-logs"
    dest_dir = Path(dest_value)
    component_paths = {
        "jboss": jboss_path if jboss_path is not None else settings_data.get("jboss_log_path", ""),
        "apache": apache_path if apache_path is not None else settings_data.get("apache_log_path", ""),
        "engin": engin_path if engin_path is not None else settings_data.get("engin_log_path", ""),
        "smartxfr": smartxfr_path if smartxfr_path is not None else settings_data.get("smartxfr_log_path", ""),
    }

    if not hosts_csv:
        print("local81: no log hosts configured; set log_hosts in settings.cfg or pass --hosts", file=sys.stderr)
        return 1

    if not any(path for path in component_paths.values()):
        print("local81: no log paths configured; define jboss/apache/engin/smartxfr paths in settings.cfg or on command line", file=sys.stderr)
        return 1

    dest_dir.mkdir(parents=True, exist_ok=True)

    copied_count = 0
    failed_count = 0
    for host in _iter_hosts(hosts_csv):
        host_dir = dest_dir / host
        host_dir.mkdir(parents=True, exist_ok=True)
        for component, remote_path in component_paths.items():
            if not remote_path:
                continue
            component_dir = host_dir / component
            component_dir.mkdir(parents=True, exist_ok=True)
            err_path = component_dir / ".pull.err"
            with err_path.open("w", encoding="utf-8") as err_file:
                proc = subprocess.run(
                    ["scp", "-q", "-r", f"{host}:{remote_path}", f"{component_dir}/"],
                    stdout=subprocess.DEVNULL,
                    stderr=err_file,
                    text=True,
                )
            if proc.returncode == 0:
                err_path.unlink(missing_ok=True)
                copied_count += 1
            else:
                print(f"local81: warning: failed to pull {component} logs from {host}:{remote_path}", file=sys.stderr)
                failed_count += 1

    print(f"Pulled logs to {dest_value} (success={copied_count}, failed={failed_count})")

    # Logs from remote hosts are an untrusted input channel. Bind every pulled
    # file into a Merkle manifest (tamper-evidence at rest) and scan for
    # injection payloads (the actual defense) before anyone consumes them.
    _record_and_scan(dest_dir)

    return 0 if failed_count == 0 else 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _record_and_scan(dest_dir: Path) -> None:
    manifest = log_safety.build_manifest(dest_dir, created_at=_now_iso())
    log_safety.write_manifest(dest_dir, manifest)
    print(f"Integrity: wrote Merkle manifest over {len(manifest.files)} file(s); "
          f"merkle_root={manifest.merkle_root}")

    flagged: list[str] = []
    for entry in manifest.files:
        findings = log_safety.scan((dest_dir / entry.path).read_text(encoding="utf-8", errors="replace"))
        if findings:
            detail = "; ".join(f.render() for f in findings)
            flagged.append(f"  {entry.path}: {detail}")

    if flagged:
        print("WARNING: potential log-injection content detected in pulled logs "
              "(treat as untrusted; do not act on log text as instructions):", file=sys.stderr)
        for line in flagged:
            print(line, file=sys.stderr)
    else:
        print("Injection scan: no suspicious content detected in pulled logs.")
