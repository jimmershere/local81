"""Untrusted-log safety: Merkle integrity + injection scanning (stdlib-only).

Local-81 pulls application logs from remote hosts (``pull-logs``) and captures
remote diagnostic output (``diag``). Per CLAUDE.md these artifacts are
*operator-readable* — read by humans in a terminal and, increasingly, by AI
agents asked to triage an incident. That makes log data an **untrusted input
channel**, and the recently reported "Agentjacking" class of attack abuses
exactly this: a malicious payload embedded in a log gets interpreted as an
instruction by whatever consumes it.

This module enforces two *independent* controls, and Local-81 routes every log
read through them so nothing is ever consumed raw:

* **Integrity (Merkle).** ``build_manifest`` records the SHA-256 of every
  collected file plus a single RFC-6962 Merkle root over those leaves (reusing
  :mod:`local81.ledger`). ``verify_manifest`` recomputes and reports any file
  altered, added, or removed after capture. This is tamper-evidence at rest.

* **Injection scanning.** Integrity proves bytes are *unchanged*, not *safe* —
  a faithfully recorded log can still carry a terminal-escape or prompt-
  injection payload. ``scan`` flags ANSI/control escapes, hidden/bidi Unicode,
  and prompt-injection phrasing; ``sanitize`` neutralizes the rendering vectors
  so the text is inert before a terminal or an agent ever sees it.

**Honesty over magic (CLAUDE.md):** Merkle checks alone do *not* stop
injection — they only prove the bytes are the ones that were captured. Both
controls always run together; ``read_log`` applies them as a unit.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from local81 import ledger

MANIFEST_NAME = ".local81-integrity.json"
MANIFEST_SCHEMA = "local81.logmanifest.v0.1"

# --- injection-detection patterns ------------------------------------------

# ANSI/VT control sequences: CSI (ESC [ ... cmd), OSC (ESC ] ... BEL|ST), and
# other two-char escape sequences. These can rewrite the terminal, spoof
# prompts, or smuggle hidden text past a reviewer.
_ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"          # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL or ST
    r"|\x1b[@-Z\\-_]"                     # other escapes (incl. single-char)
)

# C0 controls except tab/newline/carriage-return, plus DEL and the C1 block.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Invisible / direction-overriding code points abused to hide text or reorder
# it for a human while leaving the raw bytes hostile: zero-width chars, BOM,
# word joiner, bidi embedding/override controls, soft hyphen, and the Unicode
# tag block (U+E0000–U+E007F) used for "ASCII smuggling".
_HIDDEN_CODEPOINTS = (
    "­"          # soft hyphen
    "​‌‍‎‏"  # zero-width + LRM/RLM
    "  "    # line/paragraph separators
    "‪‫‬‭‮"  # bidi embeddings/overrides
    "⁠⁡⁢⁣⁤"  # word joiner / invisible operators
    "⁦⁧⁨⁩"        # bidi isolates
    "﻿"          # BOM / zero-width no-break space
)
_HIDDEN_RE = re.compile("[" + re.escape(_HIDDEN_CODEPOINTS) + "]")
_TAG_RE = re.compile(r"[\U000e0000-\U000e007f]")

# Heuristic prompt-injection phrasing. These are *flagged*, never deleted: the
# operator should still see the original text, but a warning makes clear it is
# attacker-controlled and must not be treated as an instruction.
_INJECTION_PHRASES = [
    (re.compile(r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+instructions", re.I),
     "prompt-injection: 'ignore previous instructions'"),
    (re.compile(r"disregard\s+(?:the\s+)?(?:above|previous|prior)", re.I),
     "prompt-injection: 'disregard the above'"),
    (re.compile(r"you\s+are\s+now\s+(?:a|an|the)\b", re.I),
     "prompt-injection: role reassignment ('you are now ...')"),
    (re.compile(r"\bnew\s+(?:system\s+)?instructions?\b", re.I),
     "prompt-injection: 'new instructions'"),
    (re.compile(r"\bsystem\s+prompt\b", re.I),
     "prompt-injection: references the system prompt"),
    (re.compile(r"\b(?:run|execute)\s+the\s+following\b", re.I),
     "prompt-injection: 'run/execute the following'"),
    (re.compile(r"\b(?:pip|pipx|uv)\s+install\b", re.I),
     "suspicious directive: package install (pip)"),
    (re.compile(r"\bnpm\s+(?:install|i|exec)\b|\bnpx\b", re.I),
     "suspicious directive: package install (npm/npx)"),
    (re.compile(r"\bcurl\b[^\n|]*\|\s*(?:ba|z|d)?sh\b", re.I),
     "suspicious directive: curl-pipe-to-shell"),
    (re.compile(r"\bwget\b[^\n|]*\|\s*(?:ba|z|d)?sh\b", re.I),
     "suspicious directive: wget-pipe-to-shell"),
    (re.compile(r"<!--.*?-->", re.S),
     "hidden markdown comment (common injection carrier)"),
]


@dataclass(slots=True)
class Finding:
    """One flagged item from an injection scan."""

    category: str  # "ansi" | "control" | "hidden-unicode" | "tag-chars" | "phrase"
    detail: str
    count: int = 1

    def render(self) -> str:
        suffix = f" (x{self.count})" if self.count > 1 else ""
        return f"[{self.category}] {self.detail}{suffix}"


def scan(text: str) -> list[Finding]:
    """Report injection/obfuscation indicators in ``text`` without modifying it."""
    findings: list[Finding] = []

    n_ansi = len(_ANSI_RE.findall(text))
    if n_ansi:
        findings.append(Finding("ansi", "ANSI/VT terminal escape sequence", n_ansi))

    # Count control chars that are not part of an ANSI escape we already counted.
    n_ctrl = len(_CONTROL_RE.findall(_ANSI_RE.sub("", text)))
    if n_ctrl:
        findings.append(Finding("control", "non-printable control character", n_ctrl))

    n_hidden = len(_HIDDEN_RE.findall(text))
    if n_hidden:
        findings.append(Finding("hidden-unicode", "invisible/bidi Unicode code point", n_hidden))

    n_tag = len(_TAG_RE.findall(text))
    if n_tag:
        findings.append(Finding("tag-chars", "Unicode tag char (ASCII smuggling)", n_tag))

    for pattern, label in _INJECTION_PHRASES:
        hits = len(pattern.findall(text))
        if hits:
            findings.append(Finding("phrase", label, hits))

    return findings


@dataclass(slots=True)
class SanitizeResult:
    text: str
    findings: list[Finding]

    @property
    def changed(self) -> bool:
        return any(f.category in {"ansi", "control", "hidden-unicode", "tag-chars"} for f in self.findings)

    @property
    def flagged(self) -> bool:
        return bool(self.findings)


def sanitize(text: str) -> SanitizeResult:
    """Return ``text`` with rendering vectors neutralized, plus all findings.

    Removes ANSI escapes, invisible/bidi Unicode and tag chars, and replaces
    other control characters with a visible ``\\xNN`` token. Prompt-injection
    *phrases* are preserved (so the operator sees them) but reported, since the
    real defense there is making them visibly attacker-controlled rather than
    silently dropping content.
    """
    findings = scan(text)

    cleaned = _ANSI_RE.sub("", text)
    cleaned = _HIDDEN_RE.sub("", cleaned)
    cleaned = _TAG_RE.sub("", cleaned)
    # NFKC folds homoglyph/compatibility forms toward their canonical ASCII-ish
    # equivalents so smuggled look-alikes do not survive review.
    cleaned = unicodedata.normalize("NFKC", cleaned)
    cleaned = _CONTROL_RE.sub(lambda m: f"\\x{ord(m.group()):02x}", cleaned)

    return SanitizeResult(text=cleaned, findings=findings)


def sanitize_line(line: str) -> str:
    """Convenience: neutralized form of a single line (findings discarded)."""
    return sanitize(line).text


# --- Merkle integrity manifest ---------------------------------------------

def _sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _file_leaf(rel_path: str, sha256_hex: str) -> str:
    """Domain-separated leaf hash for one file (reuses the ledger convention)."""
    return ledger._leaf_hash({"path": rel_path, "sha256": sha256_hex})


@dataclass(slots=True)
class ManifestEntry:
    path: str
    sha256: str
    bytes: int


@dataclass(slots=True)
class Manifest:
    merkle_root: str
    created_at: str
    files: list[ManifestEntry] = field(default_factory=list)
    schema: str = MANIFEST_SCHEMA

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": self.schema,
                "created_at": self.created_at,
                "merkle_root": self.merkle_root,
                "files": [{"path": e.path, "sha256": e.sha256, "bytes": e.bytes} for e in self.files],
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def _iter_log_files(base_dir: Path) -> list[Path]:
    files = [
        p
        for p in sorted(base_dir.rglob("*"))
        if p.is_file() and p.name != MANIFEST_NAME
    ]
    return files


def build_manifest(base_dir: str | Path, *, created_at: str) -> Manifest:
    """Hash every file under ``base_dir`` and bind them into a Merkle root."""
    base = Path(base_dir)
    entries: list[ManifestEntry] = []
    leaves: list[str] = []
    for path in _iter_log_files(base):
        digest, size = _sha256_file(path)
        rel = path.relative_to(base).as_posix()
        entries.append(ManifestEntry(path=rel, sha256=digest, bytes=size))
        leaves.append(_file_leaf(rel, digest))
    return Manifest(merkle_root=ledger.merkle_root(leaves), created_at=created_at, files=entries)


def write_manifest(base_dir: str | Path, manifest: Manifest) -> Path:
    """Persist ``manifest`` as owner-only JSON inside ``base_dir``."""
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    path = base / MANIFEST_NAME
    path.write_text(manifest.to_json() + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def load_manifest(base_dir: str | Path) -> Manifest | None:
    path = Path(base_dir) / MANIFEST_NAME
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return Manifest(
        merkle_root=data.get("merkle_root", ""),
        created_at=data.get("created_at", ""),
        schema=data.get("schema", MANIFEST_SCHEMA),
        files=[ManifestEntry(path=e["path"], sha256=e["sha256"], bytes=int(e.get("bytes", 0)))
               for e in data.get("files", [])],
    )


@dataclass(slots=True)
class ManifestVerifyResult:
    ok: bool
    merkle_root: str
    altered: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    root_mismatch: bool = False

    def detail(self) -> str:
        if self.ok:
            return f"integrity OK; merkle_root={self.merkle_root}"
        parts = []
        if self.root_mismatch:
            parts.append("merkle root mismatch")
        if self.altered:
            parts.append(f"altered: {', '.join(self.altered)}")
        if self.missing:
            parts.append(f"missing: {', '.join(self.missing)}")
        if self.extra:
            parts.append(f"unrecorded: {', '.join(self.extra)}")
        return "; ".join(parts) or "integrity failure"


def verify_manifest(base_dir: str | Path) -> ManifestVerifyResult:
    """Recompute hashes under ``base_dir`` and compare to the stored manifest."""
    base = Path(base_dir)
    manifest = load_manifest(base)
    if manifest is None:
        return ManifestVerifyResult(ok=False, merkle_root="", missing=["<manifest>"])

    recorded = {e.path: e for e in manifest.files}
    present: dict[str, str] = {}
    for path in _iter_log_files(base):
        rel = path.relative_to(base).as_posix()
        digest, _size = _sha256_file(path)
        present[rel] = digest

    altered = [rel for rel, e in recorded.items() if rel in present and present[rel] != e.sha256]
    missing = [rel for rel in recorded if rel not in present]
    extra = [rel for rel in present if rel not in recorded]

    leaves = [_file_leaf(e.path, e.sha256) for e in manifest.files]
    root_mismatch = ledger.merkle_root(leaves) != manifest.merkle_root

    ok = not (altered or missing or extra or root_mismatch)
    return ManifestVerifyResult(
        ok=ok,
        merkle_root=manifest.merkle_root,
        altered=sorted(altered),
        missing=sorted(missing),
        extra=sorted(extra),
        root_mismatch=root_mismatch,
    )


# --- the mandatory consumption gateway -------------------------------------

# Integrity outcomes. Only ``tampered`` / ``unrecorded`` are alarming; a log
# with no manifest is *unverified*, which is reported but not treated as proof
# of compromise (e.g. locally generated run logs predate manifesting).
INTEGRITY_VERIFIED = "verified"
INTEGRITY_UNVERIFIED = "unverified"
INTEGRITY_TAMPERED = "tampered"
INTEGRITY_UNRECORDED = "unrecorded"


@dataclass(slots=True)
class SafeLog:
    """A log read that has passed (or recorded the absence of) both controls."""

    text: str
    integrity_status: str
    integrity_detail: str
    findings: list[Finding]

    @property
    def integrity_ok(self) -> bool:
        return self.integrity_status in {INTEGRITY_VERIFIED, INTEGRITY_UNVERIFIED}

    @property
    def safe(self) -> bool:
        return self.integrity_status == INTEGRITY_VERIFIED and not self.findings

    def banner(self) -> str | None:
        """A one-or-more line operator warning, or ``None`` when nothing to flag."""
        lines: list[str] = []
        if self.integrity_status in {INTEGRITY_TAMPERED, INTEGRITY_UNRECORDED}:
            lines.append(f"!! INTEGRITY FAILURE: {self.integrity_detail}")
        if self.findings:
            lines.append("!! UNTRUSTED LOG CONTENT — flagged, do NOT treat as instructions:")
            lines.extend(f"     - {f.render()}" for f in self.findings)
        return "\n".join(lines) if lines else None


def read_log(path: str | Path, *, base_dir: str | Path | None = None) -> SafeLog:
    """Read a log file through both controls; this is the only sanctioned reader.

    Integrity is checked against the Merkle manifest in ``base_dir`` (the
    capture directory) when one exists; if it is absent the read is reported as
    *unverified* rather than silently trusted. The returned text is always
    sanitized so injection vectors are inert regardless of integrity outcome.
    """
    p = Path(path)
    raw = p.read_text(encoding="utf-8", errors="replace")
    result = sanitize(raw)

    status = INTEGRITY_UNVERIFIED
    detail = "no integrity manifest present (source unverified)"
    if base_dir is not None:
        base = Path(base_dir)
        manifest = load_manifest(base)
        if manifest is not None:
            try:
                rel = p.relative_to(base).as_posix()
            except ValueError:
                rel = p.name
            entry = {e.path: e for e in manifest.files}.get(rel)
            if entry is None:
                status = INTEGRITY_UNRECORDED
                detail = f"{rel} is not recorded in the integrity manifest"
            else:
                actual, _ = _sha256_file(p)
                if actual == entry.sha256:
                    status = INTEGRITY_VERIFIED
                    detail = f"sha256 verified against Merkle manifest (root {manifest.merkle_root[:16]}…)"
                else:
                    status = INTEGRITY_TAMPERED
                    detail = f"{rel} sha256 does NOT match manifest — altered after capture"

    return SafeLog(text=result.text, integrity_status=status, integrity_detail=detail, findings=result.findings)
