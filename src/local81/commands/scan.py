"""``local81 scan`` — Merkle integrity + injection scan for untrusted logs.

A standalone gateway over :mod:`local81.log_safety`, so the same engine that
guards ``pull-logs``/``diag``/``logs`` is reusable from CI, an n8n node, a
Claude Code skill, or the bundled MCP server. It never executes anything it
finds — it reports, and (with ``--sanitize``) emits an inert copy.

Exit codes: ``0`` clean (at/below the ``--fail-on`` threshold and integrity OK),
``3`` findings at/above the threshold, ``4`` integrity failure.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from local81 import log_safety

_SEVERITY_RANK = log_safety.SEVERITY_RANK


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iter_targets(paths: list[str]) -> list[Path]:
    targets: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            targets.extend(
                f for f in sorted(p.rglob("*"))
                if f.is_file() and f.name != log_safety.MANIFEST_NAME
            )
        elif p.is_file():
            targets.append(p)
    return targets


def run_scan(paths: list[str], *, manifest_dir: str | None = None,
             write_manifest: bool = False, verify: bool = False,
             sanitize_to: str | None = None, as_json: bool = False,
             fail_on: str = "high", quiet: bool = False) -> int:
    if not paths:
        print("local81 scan: provide one or more files or directories to scan", file=sys.stderr)
        return 2

    threshold = _SEVERITY_RANK.get(fail_on, 99)  # "never" -> not in map -> 99
    rc = 0
    report: dict = {"schema": "local81.scan.v0.1", "generated_at": _now_iso(), "files": []}

    # Optional integrity manifest handling over a single base directory.
    base = Path(manifest_dir) if manifest_dir else (Path(paths[0]) if Path(paths[0]).is_dir() else None)
    if write_manifest:
        if base is None:
            print("local81 scan: --write-manifest needs a directory (--manifest-dir or a dir path)", file=sys.stderr)
            return 2
        manifest = log_safety.build_manifest(base, created_at=_now_iso())
        log_safety.write_manifest(base, manifest)
        report["manifest_written"] = True
        report["merkle_root"] = manifest.merkle_root
        if not quiet and not as_json:
            print(f"[scan] wrote Merkle manifest over {len(manifest.files)} file(s); merkle_root={manifest.merkle_root}")

    if verify:
        if base is None:
            print("local81 scan: --verify needs a directory (--manifest-dir or a dir path)", file=sys.stderr)
            return 2
        result = log_safety.verify_manifest(base)
        report["integrity"] = {"ok": result.ok, "merkle_root": result.merkle_root, "detail": result.detail()}
        if not result.ok:
            rc = max(rc, 4)
            if not quiet and not as_json:
                print(f"[scan] INTEGRITY FAILURE: {result.detail()}", file=sys.stderr)
        elif not quiet and not as_json:
            print(f"[scan] integrity OK; merkle_root={result.merkle_root}")

    for path in _iter_targets(paths):
        text = path.read_text(encoding="utf-8", errors="replace")
        findings = log_safety.scan(text)
        sev = log_safety.max_severity(findings)
        file_entry = {
            "path": str(path),
            "max_severity": sev,
            "findings": [{"category": f.category, "detail": f.detail, "count": f.count, "severity": f.severity}
                         for f in findings],
        }
        report["files"].append(file_entry)

        if findings and sev is not None and _SEVERITY_RANK.get(sev, 0) >= threshold:
            rc = max(rc, 3)

        if findings and not quiet and not as_json:
            print(f"[scan] {path}: {len(findings)} finding(s) (max severity {sev})", file=sys.stderr)
            for f in findings:
                print(f"         - {f.render()}", file=sys.stderr)

        if sanitize_to:
            clean = log_safety.sanitize(text).text
            out_base = Path(sanitize_to)
            # Mirror a directory tree when scanning a dir; else write one file.
            if base is not None:
                try:
                    rel = path.relative_to(base)
                except ValueError:
                    rel = Path(path.name)
                dest = out_base / rel
            else:
                dest = out_base if out_base.suffix else out_base / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(clean, encoding="utf-8")

    if as_json:
        report["exit_code"] = rc
        print(json.dumps(report, indent=2))
    elif not quiet:
        flagged = sum(1 for f in report["files"] if f["findings"])
        print(f"[scan] scanned {len(report['files'])} file(s); {flagged} flagged; exit={rc}")

    return rc
