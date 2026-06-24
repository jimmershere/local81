# vendor/ — bundled third-party source for air-gapped builds

Local-81 is **stdlib-first**. The *only* third-party runtime dependency is
[PyYAML](https://pyyaml.org/), used to parse `config.yaml`, recipe catalogs, and
DB target files. To make the product buildable inside a locked-down / air-gapped
network **without pip and without network access**, that dependency is vendored
here as pure-Python source.

## What's here

- `yaml/` — PyYAML pure-Python package (see `yaml/.pyyaml-version` for the exact
  version). License: MIT, see `yaml/LICENSE`.

Only the pure-Python `.py` modules are vendored. The optional libyaml C
accelerator (`_yaml.*.so`) is intentionally **not** bundled: it is glibc-,
architecture-, and CPython-ABI-specific, which is exactly what we want to avoid
for a portable air-gapped artifact. PyYAML automatically falls back to its
pure-Python loader/dumper when the C extension is absent
(`yaml.__with_libyaml__ == False`), and Local-81 only uses `safe_load` /
`safe_dump`, both fully supported in pure-Python mode.

## How it's consumed

`packaging/zipapp/build-zipapp.sh` copies `src/local81/` and `vendor/yaml/` into
a single `local81.pyz` zipapp. No pip, no compiler, no network — just
`python3.12` and the stdlib `zipapp` module. See `packaging/zipapp/README.md`.

The normal pip install path (`pip install .`) still pulls PyYAML from
`pyproject.toml` as usual; this directory is only used by the zipapp build.

## Refreshing the vendored copy (done on a connected build host)

```bash
python3.12 -m pip download "PyYAML==<ver>" --no-binary :all: -d /tmp/pyy
# unpack, copy the pure-python yaml/*.py into vendor/yaml/, keep LICENSE
```

Then commit the result so the air-gapped network never needs pip.
