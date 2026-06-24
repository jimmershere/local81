# Air-gapped zipapp build

A pip-free way to ship Local-81 into a locked-down / air-gapped / government
secure network. Produces a single self-contained executable
(`dist/local81.pyz`) plus an offline release bundle
(`dist/local81-<ver>-py312-noarch.zip`).

## Why a zipapp (and not a "real" binary)

Local-81 is a Python 3.12 CLI that is **stdlib-first** — its only third-party
runtime dependency is PyYAML, vendored as pure-Python source under
[`vendor/yaml`](../../vendor/README.md). A stdlib
[`zipapp`](https://docs.python.org/3/library/zipapp.html) is therefore the
right packaging:

| Property | zipapp (this) | PyInstaller/Nuitka binary |
|---|---|---|
| pip at build time | **none** | required |
| network at build time | **none** | usually required |
| compiler / build host matching target glibc | **none** | required |
| architecture / kernel specific | **no** (pure source) | yes (per-glibc/arch) |
| auditable (greppable source in artifact) | **yes** | no |
| runtime requirement on target | `python3.12` | none |

Because the target government hosts already have (or can `dnf install`)
`python3.12`, the zipapp is smaller, portable, and reviewable — and the build
itself runs inside the air-gapped network with nothing but the interpreter.

The same `.pyz` validated on **RHEL 8.10 (kernel 4.18.0-553) x86_64** runs
unchanged on any other python3.12 host — it is `noarch`.

## Build

```bash
make zipapp
# or directly, with options:
packaging/zipapp/build-zipapp.sh --python python3.12 --out dist
```

Requires only `python3.12` (with the stdlib `zipapp`/`zipfile` modules). No pip,
no compiler, no network. The build stages `src/local81` + `vendor/yaml`, emits
`dist/local81.pyz` with a `#!/usr/bin/env python3.12` shebang, smoke-tests it,
then assembles the offline `.zip` bundle (`.pyz` + launcher + `INSTALL.txt` +
`MANIFEST.txt` + `SHA256SUMS`).

## Run

```bash
python3.12 dist/local81.pyz --help
./dist/local81.pyz --help          # shebang makes it directly executable
```

## Deliver into the secure network

Transfer `dist/local81-<ver>-py312-noarch.zip` via your approved media/transfer
process. On the target: `unzip`, `sha256sum -c SHA256SUMS`, then follow
`INSTALL.txt`.

## Audit

```bash
unzip -l dist/local81.pyz                      # inventory
unzip -p dist/local81.pyz local81/runner.py    # read any source file
python3.12 -m zipfile -l dist/local81.pyz
```

Everything inside is plain `.py` source (no bytecode), so a reviewer can read
every line that will execute.

## Maintenance

- The vendored PyYAML is refreshed on a connected host — see
  [`vendor/README.md`](../../vendor/README.md). Commit the result so the
  air-gapped network never needs pip.
- If Local-81 ever gains a new third-party runtime import, vendor it the same
  way and copy it into the stage in `build-zipapp.sh`. Per repo policy every new
  runtime dependency needs a one-line justification in its PR.
