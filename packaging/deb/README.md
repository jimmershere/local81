# Local-81 Debian package
This directory builds an internal Ubuntu/Debian `.deb` package for Local-81.
## Packaging model
The package installs:
- `/usr/bin/local81`, wrapper entrypoint
- `/opt/local81/app`, application source and operator docs
- `/opt/local81/venv`, isolated Python runtime populated during package build
- `/etc/local81/local81.ini.example`, packaged config example
- `/var/lib/local81`, reserved packaged runtime state directory
## Build prerequisites
Expected on the build host:
- `dpkg-deb`
- `python3.12`
- `python3.12-venv`
- `pip` access to build Python dependencies
- `rsync`
## Build
From the repository root:
```bash
./packaging/deb/build-deb.sh
```
Artifacts land under:
```text
packaging/deb/.debbuild/artifacts/
```
## Smoke test
The smoke test extracts the package into a temporary directory and runs the packaged wrapper with `LOCAL81_HOME` pointed at the extracted payload, so it does not install into the host system:
```bash
./packaging/deb/test-deb.sh packaging/deb/.debbuild/artifacts/local81_0.1.0-2_all.deb
```

## Air-gapped / pip-free alternative

This `.deb` builds an application virtualenv and therefore needs `pip` (and
network) at build time. For locked-down or air-gapped networks where pip is
unavailable, use the self-contained zipapp instead — see
[`../zipapp/README.md`](../zipapp/README.md) (`make zipapp`).
