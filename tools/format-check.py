#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Vendored third-party source (see vendor/README.md) is kept byte-faithful to
# upstream and is not subject to Local-81 formatting rules.
EXCLUDED_PREFIXES = ("vendor/",)

TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".service",
    ".sh",
    ".spec",
    ".svg",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def _git_candidate_files() -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def main() -> int:
    findings: list[str] = []
    for rel in _git_candidate_files():
        if rel.startswith(EXCLUDED_PREFIXES):
            continue
        path = ROOT / rel
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        data = path.read_bytes()
        if data and not data.endswith(b"\n"):
            findings.append(f"{rel}: missing final newline")
        if b"\r\n" in data:
            findings.append(f"{rel}: uses CRLF line endings")
        for line_number, line in enumerate(data.splitlines(), 1):
            if line.rstrip(b" \t") != line:
                findings.append(f"{rel}:{line_number}: trailing whitespace")
    if findings:
        print("Local-81 format check failed:")
        for finding in findings:
            print(f"  - {finding}")
        return 1
    print("Local-81 format check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
