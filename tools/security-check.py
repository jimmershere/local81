#!/usr/bin/env python3
from __future__ import annotations

import ast
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GENERATED_PATTERNS = (
    "__pycache__/*",
    "*.pyc",
    "*.egg-info/*",
    "tools/SF-*.b64.txt",
    "build/*",
    "dist/*",
    "packaging/rpm/*.rpm",
)

# Vendored third-party source (see vendor/README.md) is trusted upstream code,
# kept byte-faithful; it is not subject to Local-81's source-style scans.
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

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |)PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bASIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}\b"),
)

APPROVED_BASH_LC_CONTEXTS = {
    ("src/local81/commands/deploy.py", "_run_shell"),
}


def _git_ls_files() -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _is_generated(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in GENERATED_PATTERNS)


def _tracked_generated_findings(paths: list[str]) -> list[str]:
    return [f"tracked generated artifact: {path}" for path in paths if _is_generated(path)]


def _iter_text_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        candidate = ROOT / path
        if candidate.suffix in TEXT_SUFFIXES and candidate.is_file():
            files.append(candidate)
    return files


def _secret_findings(paths: list[str]) -> list[str]:
    findings: list[str] = []
    for file_path in _iter_text_files(paths):
        rel = file_path.relative_to(ROOT).as_posix()
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(content.splitlines(), 1):
            for pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(f"{rel}:{line_number}: possible hard-coded secret")
    return findings


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _is_bash_lc_call(node: ast.Call) -> bool:
    if not node.args:
        return False
    first = node.args[0]
    if not isinstance(first, ast.List) or len(first.elts) < 2:
        return False
    first_two: list[str] = []
    for elt in first.elts[:2]:
        if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
            return False
        first_two.append(elt.value)
    return first_two == ["bash", "-lc"]


def _function_context(tree: ast.AST, node: ast.AST) -> str:
    node_line = getattr(node, "lineno", 0)
    matches: list[tuple[int, str]] = []
    for candidate in ast.walk(tree):
        if isinstance(candidate, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = candidate.lineno
            end = getattr(candidate, "end_lineno", start)
            if start <= node_line <= end:
                matches.append((start, candidate.name))
    if not matches:
        return "<module>"
    return sorted(matches)[-1][1]


def _python_security_findings(paths: list[str]) -> list[str]:
    findings: list[str] = []
    for rel in paths:
        if not rel.endswith(".py"):
            continue
        file_path = ROOT / rel
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError as exc:
            findings.append(f"{rel}:{exc.lineno}: Python syntax error: {exc.msg}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node.func)
            for keyword in node.keywords:
                if keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                    findings.append(f"{rel}:{node.lineno}: subprocess shell=True is forbidden")
            if call_name in {"os.system", "os.popen"}:
                findings.append(f"{rel}:{node.lineno}: {call_name} is forbidden")
            if _is_bash_lc_call(node) and (rel, _function_context(tree, node)) not in APPROVED_BASH_LC_CONTEXTS:
                findings.append(f"{rel}:{node.lineno}: new bash -lc execution site must be reviewed and approved")
    return findings


def main() -> int:
    tracked_paths = _git_ls_files()
    # Generated-artifact detection still runs over everything; source-style scans
    # skip vendored third-party trees.
    scan_paths = [p for p in tracked_paths if not p.startswith(EXCLUDED_PREFIXES)]
    findings = [
        *_tracked_generated_findings(tracked_paths),
        *_secret_findings(scan_paths),
        *_python_security_findings(scan_paths),
    ]
    if findings:
        print("Local-81 security check failed:")
        for finding in findings:
            print(f"  - {finding}")
        return 1
    print("Local-81 security check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
