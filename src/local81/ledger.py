"""Immutable, tamper-evident audit ledger (stdlib-only).

Two cryptographic structures over one human-greppable file
(``.local81/audit/ledger.jsonl`` — one JSON object per line):

* **Hash chain.** Each entry binds the previous entry's hash, so altering or
  removing any past entry breaks every entry after it. ``verify_chain`` walks
  the file and reports the first divergence. This is the cheap, linear
  tamper-evidence operators run routinely.
* **Merkle tree (RFC 6962-style).** The per-entry *leaf* hashes form a Merkle
  tree whose single root fingerprints the whole log. ``inclusion_proof`` /
  ``verify_inclusion`` prove a specific entry is in the log in O(log n) without
  shipping the whole ledger — the basis for ``audit prove`` and signed receipts.

Domain separation follows RFC 6962: leaves are hashed with a ``0x00`` prefix,
internal nodes with ``0x01``; the chain link uses ``0x02``. Entries may be HMAC
*signed* (authenticity, not just integrity) when a key is supplied — the key is
never written, only the signature.

The ledger stores high-level events (e.g. a run summary plus the SHA-256 of the
already-scrubbed run.json), never secret material — it indexes artifacts, it
does not replace them.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path

GENESIS = "0" * 64
LEDGER_RELPATH = Path(".local81") / "audit" / "ledger.jsonl"
SCHEMA = "local81.audit.v0.1"


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _leaf_hash(core: dict) -> str:
    # 0x00 || canonical(core)
    return hashlib.sha256(b"\x00" + _canon(core)).hexdigest()


def _hash_pair(left: str, right: str) -> str:
    # 0x01 || left || right
    return hashlib.sha256(b"\x01" + bytes.fromhex(left) + bytes.fromhex(right)).hexdigest()


def _chain_hash(prev_hash: str, leaf: str) -> str:
    # 0x02 || prev || leaf
    return hashlib.sha256(b"\x02" + prev_hash.encode("ascii") + leaf.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class Signer:
    """HMAC-SHA256 signer; the key stays in memory and is never persisted."""

    key: bytes

    def sign(self, message: str) -> str:
        return hmac.new(self.key, message.encode("ascii"), hashlib.sha256).hexdigest()

    def verify(self, message: str, signature: str) -> bool:
        return hmac.compare_digest(self.sign(message), signature or "")


def ledger_path(root: str | Path = ".") -> Path:
    return Path(root) / LEDGER_RELPATH


def load_entries(root: str | Path = ".") -> list[dict]:
    path = ledger_path(root)
    if not path.is_file():
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def append_event(event: dict, *, ts: str, root: str | Path = ".", signer: Signer | None = None) -> dict:
    """Append one event, linking it to the chain head. Returns the written entry.

    ``ts`` is supplied by the caller (the module never reads the clock, keeping
    it deterministic and testable).
    """
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    entries = load_entries(root)
    seq = len(entries)
    prev = entries[-1]["hash"] if entries else GENESIS
    core = {"schema": SCHEMA, "seq": seq, "ts": ts, "event": event}
    leaf = _leaf_hash(core)
    entry_hash = _chain_hash(prev, leaf)
    entry = {**core, "prev": prev, "leaf": leaf, "hash": entry_hash}
    if signer is not None:
        entry["sig"] = signer.sign(entry_hash)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
    path.chmod(0o600)
    return entry


@dataclass(slots=True)
class VerifyResult:
    ok: bool
    count: int
    root: str
    detail: str = ""
    bad_seq: int | None = None


def _recompute_entry(prev: str, stored: dict) -> tuple[str, str]:
    core = {"schema": stored.get("schema", SCHEMA), "seq": stored["seq"],
            "ts": stored["ts"], "event": stored["event"]}
    leaf = _leaf_hash(core)
    return leaf, _chain_hash(prev, leaf)


def verify_chain(root: str | Path = ".", *, signer: Signer | None = None) -> VerifyResult:
    """Walk the chain; report the first entry that does not recompute."""
    entries = load_entries(root)
    if not entries:
        return VerifyResult(ok=True, count=0, root=GENESIS, detail="empty ledger")
    prev = GENESIS
    for i, stored in enumerate(entries):
        if stored.get("seq") != i:
            return VerifyResult(False, len(entries), GENESIS, f"seq mismatch at index {i}: {stored.get('seq')!r}", i)
        if stored.get("prev") != prev:
            return VerifyResult(False, len(entries), GENESIS, f"broken link at seq {i}", i)
        leaf, entry_hash = _recompute_entry(prev, stored)
        if leaf != stored.get("leaf") or entry_hash != stored.get("hash"):
            return VerifyResult(False, len(entries), GENESIS, f"tampered content at seq {i}", i)
        if signer is not None and not signer.verify(entry_hash, stored.get("sig", "")):
            return VerifyResult(False, len(entries), GENESIS, f"bad signature at seq {i}", i)
        prev = stored["hash"]
    return VerifyResult(True, len(entries), compute_root(root), detail="chain intact")


# --- Merkle tree ------------------------------------------------------------

def merkle_root(leaves: list[str]) -> str:
    if not leaves:
        return GENESIS
    nodes = list(leaves)
    while len(nodes) > 1:
        nxt: list[str] = []
        for i in range(0, len(nodes), 2):
            if i + 1 < len(nodes):
                nxt.append(_hash_pair(nodes[i], nodes[i + 1]))
            else:
                nxt.append(nodes[i])  # lone node promoted unchanged (RFC 6962)
        nodes = nxt
    return nodes[0]


def inclusion_proof(leaves: list[str], index: int) -> list[tuple[str, str]]:
    """Audit path for ``leaves[index]``: list of (sibling_hash, 'L'|'R')."""
    if not 0 <= index < len(leaves):
        raise IndexError(index)
    proof: list[tuple[str, str]] = []
    nodes = list(leaves)
    idx = index
    while len(nodes) > 1:
        nxt: list[str] = []
        for i in range(0, len(nodes), 2):
            if i + 1 < len(nodes):
                nxt.append(_hash_pair(nodes[i], nodes[i + 1]))
                if i == idx:
                    proof.append((nodes[i + 1], "R"))
                elif i + 1 == idx:
                    proof.append((nodes[i], "L"))
            else:
                nxt.append(nodes[i])  # promoted; no sibling for idx==i
        idx //= 2
        nodes = nxt
    return proof


def verify_inclusion(leaf: str, proof: list[tuple[str, str]], root: str) -> bool:
    h = leaf
    for sibling, side in proof:
        h = _hash_pair(sibling, h) if side == "L" else _hash_pair(h, sibling)
    return hmac.compare_digest(h, root)


def compute_root(root: str | Path = ".") -> str:
    return merkle_root([e["leaf"] for e in load_entries(root)])


def head(root: str | Path = ".") -> str:
    entries = load_entries(root)
    return entries[-1]["hash"] if entries else GENESIS
