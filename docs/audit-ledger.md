# Immutable audit ledger

Local-81 keeps a tamper-evident record of what it did in
`.local81/audit/ledger.jsonl` — one JSON object per line, human-greppable, with
two cryptographic structures layered over it (pure stdlib `hashlib`/`hmac`, no
new dependencies).

## What it guarantees

- **Hash chain** — every entry binds the previous entry's hash, so editing or
  deleting any past entry breaks every entry after it. `local81 audit verify`
  walks the file and reports the first divergence.
- **Merkle tree** (RFC 6962-style domain separation) over the per-entry leaf
  hashes — a single root fingerprints the whole log, and `local81 audit prove`
  produces an O(log n) inclusion proof for one run that verifies against that
  root without shipping the whole ledger.
- **Optional HMAC signing** — with `--hmac-key-ref` (a secret reference, never a
  literal), entries/receipts are signed for authenticity. The key is resolved in
  memory; only signatures are ever written or sent.

The ledger stores **summaries**, not secrets: each deploy appends a
`deploy_run` event carrying the run id, plan id, rc, and the **SHA-256 of the
already-scrubbed `run.json`**. It is content-addressed to the artifact — it
indexes runs, it never duplicates or leaks their contents.

## Commands

```bash
local81 audit verify                 # recompute chain + Merkle root; nonzero on tamper
local81 audit root                   # print merkle root, chain head, entry count
local81 audit show --limit 20        # recent entries
local81 audit prove --run-id <id>    # inclusion proof for a run (or --seq N)
local81 audit emit --to https://collector/receipts [--hmac-key-ref env://KEY]
```

`audit verify` and `audit emit` accept `--hmac-key-ref` to also check/sign with
HMAC. `audit emit` is the **outbound** half: it POSTs a signed receipt
(`{merkle_root, head, count, generated_at, sig}`) to a collector over HTTPS — the
control node publishes its audit root; nothing inbound is required on endpoints.
`--dry-run` prints the receipt instead of sending it.

## How it ties together

- The deploy runner appends a ledger entry after writing `run.json`
  (best-effort: a ledger failure never fails a good deploy).
- Because `run.json` is already secret-scrubbed, and the ledger only stores its
  hash, the "secrets never at rest" guarantee extends cleanly to the audit
  trail.
- The n8n workflow's "emit audit receipt" node and a Semaphore post-task hook
  can both call `local81 audit emit`, so the receipt flows to whatever collector
  or webhook the operator runs.
