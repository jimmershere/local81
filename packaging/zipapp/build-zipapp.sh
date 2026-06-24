#!/usr/bin/env bash
# Build a self-contained Local-81 zipapp (.pyz) and an offline release bundle (.zip).
#
# DESIGN GOALS (for locked-down / air-gapped / government secure networks):
#   * NO pip, NO compiler, NO network at build time -- only python3.12 + stdlib.
#   * NO third-party install at runtime -- PyYAML is vendored (vendor/yaml).
#   * Architecture / kernel independent: the .pyz is pure Python source, so the
#     same artifact runs on RHEL 8.10 (kernel 4.18.0-553) x86_64 and anywhere
#     else that has python3.12. Nothing is compiled against glibc.
#   * Auditable: source is kept inside the archive (no .pyc), so reviewers can
#     `unzip -l local81.pyz` and `unzip -p local81.pyz <file>` to read every byte.
#
# Usage:
#   packaging/zipapp/build-zipapp.sh [--python python3.12] [--out dist]
#
# Outputs (default in ./dist):
#   local81.pyz                         the executable zipapp
#   local81-<ver>-py312-noarch.zip      offline bundle (.pyz + launcher + docs + sums)
set -euo pipefail

PYTHON="python3.12"
OUT_DIR="dist"
SHEBANG="/usr/bin/env python3.12"

while [ $# -gt 0 ]; do
  case "$1" in
    --python) PYTHON="$2"; shift 2 ;;
    --out)    OUT_DIR="$2"; shift 2 ;;
    --shebang) SHEBANG="$2"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "build-zipapp: unknown arg: $1" >&2; exit 2 ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "build-zipapp: required interpreter '$PYTHON' not found on PATH" >&2
  echo "  On RHEL 8.10: sudo dnf install python3.12   (then re-run)" >&2
  exit 127
fi

# zipapp is stdlib; verify the interpreter is new enough and has it.
"$PYTHON" - <<'PY'
import sys, zipapp  # noqa: F401
assert sys.version_info[:2] >= (3, 12), f"need python>=3.12, have {sys.version.split()[0]}"
PY

version="$("$PYTHON" - <<'PY'
import pathlib, re
text = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
m = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
print(m.group(1) if m else "0.0.0")
PY
)"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT

echo "==> staging application + vendored deps"
# Application package (pure source -- no __pycache__).
cp -r src/local81 "$stage/local81"
# Vendored third-party (PyYAML pure-python). Build fails loudly if missing.
if [ ! -d vendor/yaml ]; then
  echo "build-zipapp: vendor/yaml not found -- nothing to bundle for offline build" >&2
  exit 1
fi
cp -r vendor/yaml "$stage/yaml"

# Strip any stray bytecode / caches so the archive is pure, greppable source.
find "$stage" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$stage" -name '*.pyc' -delete 2>/dev/null || true

mkdir -p "$OUT_DIR"
pyz="$OUT_DIR/local81.pyz"

echo "==> building zipapp -> $pyz"
"$PYTHON" -m zipapp "$stage" \
  --output "$pyz" \
  --python "$SHEBANG" \
  --main "local81.cli:main"
chmod 0755 "$pyz"

# Smoke test the freshly built artifact -- must run with ONLY the interpreter.
echo "==> smoke-testing $pyz"
env -i PATH="$PATH" "$PYTHON" "$pyz" --help >/dev/null
echo "    --help OK"

# ---- Offline release bundle (the "zip of binaries" deliverable) -------------
bundle_name="local81-${version}-py312-noarch"
bundle_dir="$stage/$bundle_name"
mkdir -p "$bundle_dir"
cp "$pyz" "$bundle_dir/local81.pyz"

# Tiny launcher so operators can run `./local81` instead of typing the interpreter.
cat > "$bundle_dir/local81" <<'LAUNCH'
#!/usr/bin/env bash
# Local-81 offline launcher. Runs the bundled zipapp with the system python3.12.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for py in python3.12 python3; do
  if command -v "$py" >/dev/null 2>&1; then exec "$py" "$here/local81.pyz" "$@"; fi
done
echo "local81: python3.12 is required (RHEL 8.10: sudo dnf install python3.12)" >&2
exit 127
LAUNCH
chmod 0755 "$bundle_dir/local81"

cp packaging/zipapp/INSTALL.txt "$bundle_dir/INSTALL.txt"

# Greppable manifest of everything inside the .pyz (for security review).
{
  echo "Local-81 offline bundle"
  echo "version: ${version}"
  echo "built-with: $("$PYTHON" -V 2>&1)"
  echo "bundled PyYAML: $(cat vendor/yaml/.pyyaml-version 2>/dev/null || echo unknown) (pure-python)"
  echo "target: any Linux x86_64 with python3.12 (validated for RHEL 8.10 / kernel 4.18.0-553)"
  echo
  echo "Contents of local81.pyz:"
  "$PYTHON" -m zipfile -l "$pyz"
} > "$bundle_dir/MANIFEST.txt"

# Checksums.
( cd "$bundle_dir" && for f in local81.pyz local81 INSTALL.txt MANIFEST.txt; do
    "$PYTHON" - "$f" <<'PY' >> SHA256SUMS
import hashlib, sys
p = sys.argv[1]
print(f"{hashlib.sha256(open(p,'rb').read()).hexdigest()}  {p}")
PY
  done )

zip_out="$OUT_DIR/${bundle_name}.zip"
rm -f "$zip_out"
echo "==> packaging offline bundle -> $zip_out"
# Use python stdlib zipfile (no `zip` binary dependency on the build host).
"$PYTHON" - "$stage" "$bundle_name" "$(cd "$OUT_DIR" && pwd)/${bundle_name}.zip" <<'PY'
import sys, zipfile, pathlib
stage, name, out = sys.argv[1], sys.argv[2], sys.argv[3]
root = pathlib.Path(stage) / name
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(root.rglob("*")):
        if p.is_file():
            zi = zipfile.ZipInfo(str(pathlib.Path(name) / p.relative_to(root)))
            # preserve exec bits on launcher + pyz
            mode = 0o755 if p.name in ("local81", "local81.pyz") else 0o644
            zi.external_attr = mode << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, p.read_bytes())
print("wrote", out)
PY

echo
echo "==> done"
echo "    zipapp : $pyz"
echo "    bundle : $zip_out"
echo "    run    : $PYTHON $pyz --help    (or unzip the bundle and ./local81 --help)"
