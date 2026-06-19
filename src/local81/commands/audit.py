"""``local81 audit`` — operate on the immutable Merkle audit ledger.

* ``verify`` — recompute the hash chain (and signatures, if a key is given) and
  report the first divergence; prints the current Merkle root.
* ``root``   — print the Merkle root, chain head, and entry count.
* ``show``   — print the most recent entries (human-greppable).
* ``prove``  — emit and verify an inclusion proof for one run/entry.
* ``emit``   — POST a (optionally HMAC-signed) receipt {root, head, count} to a
  collector over HTTPS; the outbound half of the audit trail.

The HMAC key, when used, comes from a secret reference (never a literal) and is
resolved in memory; only signatures are ever written or sent.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from local81 import ledger
from local81.ledger import Signer


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _signer_from_ref(hmac_key_ref: str | None) -> Signer | None:
    if not hmac_key_ref:
        return None
    from local81.secrets import SecretResolver, is_secret_ref
    from local81.secrets.errors import SecretRefSyntaxError

    if not is_secret_ref(hmac_key_ref):
        raise SecretRefSyntaxError(
            f"--hmac-key-ref must be a secret reference (env://, delinea://, ...), not a literal: {hmac_key_ref!r}"
        )
    key = SecretResolver().resolve(hmac_key_ref)
    return Signer(key=key.encode("utf-8"))


def _find_index(entries: list[dict], *, run_id: str | None, seq: int | None) -> int | None:
    if seq is not None:
        return seq if 0 <= seq < len(entries) else None
    if run_id is not None:
        for i, e in enumerate(entries):
            if e.get("event", {}).get("run_id") == run_id:
                return i
    return None


def run_audit(action: str, *, run_id: str | None = None, seq: int | None = None,
              to_url: str | None = None, hmac_key_ref: str | None = None,
              limit: int = 10, dry_run: bool = False) -> int:
    try:
        signer = _signer_from_ref(hmac_key_ref)
    except Exception as exc:  # noqa: BLE001 - surface ref/backend errors cleanly
        print(f"[fail] {exc}")
        return 1

    if action == "verify":
        result = ledger.verify_chain(signer=signer)
        if result.count == 0:
            print("Local-81 audit: ledger is empty (nothing to verify).")
            return 0
        if result.ok:
            print(f"[ok] audit ledger intact: {result.count} entr(y/ies), chain + Merkle consistent")
            print(f"     merkle_root={result.root}")
            if signer is not None:
                print("     signatures verified")
            return 0
        print(f"[FAIL] audit ledger TAMPERED: {result.detail} (seq {result.bad_seq})")
        return 1

    if action == "root":
        entries = ledger.load_entries()
        print(f"entries={len(entries)}")
        print(f"head={ledger.head()}")
        print(f"merkle_root={ledger.compute_root()}")
        return 0

    if action == "show":
        entries = ledger.load_entries()
        if not entries:
            print("Local-81 audit: ledger is empty.")
            return 0
        for e in entries[-max(1, limit):]:
            ev = e.get("event", {})
            print(f"seq={e['seq']} ts={e['ts']} hash={e['hash'][:16]}… "
                  f"kind={ev.get('kind', '?')} run_id={ev.get('run_id', '-')}")
        return 0

    if action == "prove":
        entries = ledger.load_entries()
        idx = _find_index(entries, run_id=run_id, seq=seq)
        if idx is None:
            print("Local-81 audit prove: no matching entry (use --run-id or --seq).")
            return 1
        leaves = [e["leaf"] for e in entries]
        proof = ledger.inclusion_proof(leaves, idx)
        root = ledger.merkle_root(leaves)
        ok = ledger.verify_inclusion(leaves[idx], proof, root)
        print(json.dumps({
            "seq": idx,
            "run_id": entries[idx].get("event", {}).get("run_id"),
            "leaf": leaves[idx],
            "merkle_root": root,
            "proof": [{"sibling": s, "side": side} for s, side in proof],
            "verified": ok,
        }, indent=2))
        return 0 if ok else 1

    if action == "emit":
        if not to_url:
            print("Local-81 audit emit: --to URL is required.")
            return 1
        entries = ledger.load_entries()
        receipt = {
            "schema": "local81.audit.receipt.v0.1",
            "generated_at": _now_iso(),
            "count": len(entries),
            "head": ledger.head(),
            "merkle_root": ledger.compute_root(),
        }
        if signer is not None:
            receipt["sig"] = signer.sign(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        body = json.dumps(receipt).encode("utf-8")
        if dry_run:
            print("[plan] would POST audit receipt:")
            print(json.dumps(receipt, indent=2))
            print(f"[plan] -> {to_url} (re-run without --dry-run to send)")
            return 0
        request = urllib.request.Request(
            to_url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                print(f"[ok] receipt emitted to {to_url} (HTTP {resp.status}); merkle_root={receipt['merkle_root']}")
                return 0
        except urllib.error.HTTPError as exc:
            print(f"[fail] collector returned HTTP {exc.code} for {to_url}")
            return 1
        except urllib.error.URLError as exc:
            print(f"[fail] could not reach {to_url}: {exc.reason}")
            return 1

    print(f"Local-81 audit: unknown action {action!r}")
    return 2
