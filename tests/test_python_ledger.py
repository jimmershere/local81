from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from local81 import ledger
from local81.commands.audit import run_audit
from local81.commands.deploy import run_deploy
from local81.ledger import Signer


# --- chain ----------------------------------------------------------------

def test_append_links_chain_and_verifies(tmp_path: Path) -> None:
    e0 = ledger.append_event({"kind": "x", "n": 1}, ts="2026-01-01T00:00:00Z", root=tmp_path)
    e1 = ledger.append_event({"kind": "x", "n": 2}, ts="2026-01-01T00:00:01Z", root=tmp_path)
    assert e0["seq"] == 0 and e0["prev"] == ledger.GENESIS
    assert e1["seq"] == 1 and e1["prev"] == e0["hash"]
    result = ledger.verify_chain(root=tmp_path)
    assert result.ok and result.count == 2
    assert result.root == ledger.compute_root(root=tmp_path)


def test_tampering_with_a_past_event_is_detected(tmp_path: Path) -> None:
    ledger.append_event({"kind": "x", "n": 1}, ts="t0", root=tmp_path)
    ledger.append_event({"kind": "x", "n": 2}, ts="t1", root=tmp_path)
    ledger.append_event({"kind": "x", "n": 3}, ts="t2", root=tmp_path)
    path = ledger.ledger_path(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["event"]["n"] = 999  # forge the payload, keep the stored hash
    lines[1] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = ledger.verify_chain(root=tmp_path)
    assert not result.ok
    assert result.bad_seq == 1


def test_deleting_an_event_breaks_the_chain(tmp_path: Path) -> None:
    for n in range(3):
        ledger.append_event({"kind": "x", "n": n}, ts=f"t{n}", root=tmp_path)
    path = ledger.ledger_path(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[1]  # remove the middle entry
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert not ledger.verify_chain(root=tmp_path).ok


# --- signatures ------------------------------------------------------------

def test_signed_chain_verifies_and_detects_forgery(tmp_path: Path) -> None:
    signer = Signer(key=b"k")
    ledger.append_event({"n": 1}, ts="t0", root=tmp_path, signer=signer)
    ledger.append_event({"n": 2}, ts="t1", root=tmp_path, signer=signer)
    assert ledger.verify_chain(root=tmp_path, signer=signer).ok
    # a different key fails signature verification
    assert not ledger.verify_chain(root=tmp_path, signer=Signer(key=b"other")).ok


# --- Merkle proofs ---------------------------------------------------------

@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 8, 9])
def test_inclusion_proof_round_trips_for_every_leaf(count: int) -> None:
    leaves = [ledger._leaf_hash({"i": i}) for i in range(count)]
    root = ledger.merkle_root(leaves)
    for i in range(count):
        proof = ledger.inclusion_proof(leaves, i)
        assert ledger.verify_inclusion(leaves[i], proof, root)
    # a wrong leaf must not verify against the same proof
    if count > 1:
        bad_proof = ledger.inclusion_proof(leaves, 0)
        assert not ledger.verify_inclusion(leaves[1], bad_proof, root)


# --- audit command ---------------------------------------------------------

def test_audit_verify_and_prove(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    ledger.append_event({"kind": "deploy_run", "run_id": "R1"}, ts="t0", root=tmp_path)
    ledger.append_event({"kind": "deploy_run", "run_id": "R2"}, ts="t1", root=tmp_path)

    assert run_audit("verify") == 0
    assert "intact" in capsys.readouterr().out

    assert run_audit("prove", run_id="R2") == 0
    proof_out = json.loads(capsys.readouterr().out)
    assert proof_out["seq"] == 1 and proof_out["verified"] is True


def test_audit_verify_fails_on_tamper(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    ledger.append_event({"kind": "x", "n": 1}, ts="t0", root=tmp_path)
    path = ledger.ledger_path(tmp_path)
    rec = json.loads(path.read_text().splitlines()[0])
    rec["event"]["n"] = 2
    path.write_text(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    assert run_audit("verify") == 1
    assert "TAMPERED" in capsys.readouterr().out


# --- deploy integration -----------------------------------------------------

def test_deploy_appends_verifiable_ledger_entry(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "schema": "local81.plan.v0.1", "kind": "plan", "mode": "deploy", "plan_id": "p1",
        "scopes": [{"scope": "web", "steps": [
            {"id": "scope:web:0001", "type": "rsync", "host": "local", "cmd": "printf ok"}]}],
    }), encoding="utf-8")

    assert run_deploy(plan=str(plan), scope="web", dry_run=False) == 0
    capsys.readouterr()

    entries = ledger.load_entries(tmp_path)
    assert len(entries) == 1
    ev = entries[0]["event"]
    assert ev["kind"] == "deploy_run" and ev["plan_id"] == "p1"
    assert ev["run_json_sha256"]  # content-addressed to the (scrubbed) run.json
    assert ledger.verify_chain(root=tmp_path).ok


# --- emit over a mock collector ---------------------------------------------

class _Collector(BaseHTTPRequestHandler):
    received: list[dict] = []

    def log_message(self, *_a) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        _Collector.received.append(json.loads(self.rfile.read(length).decode("utf-8")))
        self.send_response(200)
        self.end_headers()


def test_audit_emit_posts_signed_receipt(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("L81_AUDIT_KEY", "supersecretkey")
    ledger.append_event({"kind": "deploy_run", "run_id": "R1"}, ts="t0", root=tmp_path)

    _Collector.received.clear()
    server = HTTPServer(("127.0.0.1", 0), _Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    try:
        rc = run_audit("emit", to_url=f"http://{host}:{port}/receipts", hmac_key_ref="env://L81_AUDIT_KEY")
    finally:
        server.shutdown()

    assert rc == 0
    assert len(_Collector.received) == 1
    receipt = _Collector.received[0]
    assert receipt["merkle_root"] == ledger.compute_root(root=tmp_path)
    assert receipt["count"] == 1
    assert "sig" in receipt  # HMAC-signed with the resolved key
    # the signing key itself is never transmitted
    assert "supersecretkey" not in json.dumps(receipt)


def test_audit_emit_dry_run_does_not_send(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    ledger.append_event({"kind": "x"}, ts="t0", root=tmp_path)
    rc = run_audit("emit", to_url="http://127.0.0.1:1/never", dry_run=True)
    assert rc == 0
    assert "would POST" in capsys.readouterr().out
