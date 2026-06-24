# Local-81 RPM scaffold

This directory contains the first operator-grade RPM packaging scaffold for Local-81.

## Packaging model

Current install layout:

- `/usr/bin/local81`, operator entrypoint wrapper
- `/opt/local81/app`, application source, docs, and packaging payload
- `/opt/local81/venv`, isolated runtime virtualenv populated at package build time
- `/etc/local81/local81.ini.example`, packaged config example
- `/var/lib/local81`, runtime state directory

Because the package embeds an application virtualenv with Python executables and native dependency artifacts, the RPM is architecture-specific rather than `noarch`.

RPM debug package and build-id link generation are disabled for this scaffold because Local-81 ships an application virtualenv, not project-compiled native binaries with useful debuginfo.

## Why this layout

Local-81 is not yet shaped like a native system Python package for stock RHEL8. It currently:

- targets Python `>=3.12`
- depends on `PyYAML`
- carries both Python and shell entrypoints
- is better presented as an application bundle under `/opt/local81`

That makes an `/opt/local81` payload with a stable `/usr/bin/local81` wrapper the safest first professional package shape.

## Build prerequisites

Expected on the RPM build host:

- `rpmbuild`
- `python3`
- `python3.12`
- `python3.12-devel`
- `python3.12-pip`
- `python3.12-setuptools`
- `python3.12-wheel`
- `tar`
- `rsync`

## Local build

From the repo root:

```bash
chmod +x packaging/rpm/build-rpm.sh
./packaging/rpm/build-rpm.sh
```

Artifacts, when the toolchain is present, land under:

```text
packaging/rpm/.rpmbuild/RPMS/
packaging/rpm/.rpmbuild/SRPMS/
```

## Rocky container build

If Docker is available:

```bash
./packaging/rpm/build-rpm-container.sh
```

The helper defaults to `docker run --rm rockylinux:9`, installs the RPM build dependencies inside the container, runs `packaging/rpm/test-rpm.sh`, and leaves artifacts under `packaging/rpm/.rpmbuild/`.

You can override the runtime or image:

```bash
CONTAINER_RUNTIME=podman LOCAL81_RPM_CONTAINER_IMAGE=rockylinux:9 ./packaging/rpm/build-rpm-container.sh
```

## Air-gapped / pip-free alternative

This RPM embeds an application virtualenv populated by `pip` during the build,
so the build host needs pip and (typically) network access to PyPI. For
locked-down or air-gapped networks where pip is unavailable, ship the
self-contained zipapp instead — see [`../zipapp/README.md`](../zipapp/README.md)
(`make zipapp`); it vendors PyYAML and needs only `python3.12`.

## Validation

From the repo root:

```bash
./packaging/rpm/test-rpm.sh
```

The validation helper always checks script syntax and verifies that the spec version matches `pyproject.toml`. If `rpmbuild` is available, it builds the RPM and inspects the resulting package. If `rpmbuild` is missing, it reports an explicit skip without treating the local machine as a failed RHEL builder.

## Current known blockers

1. The project requires Python 3.12+, which is not a standard base RHEL8 runtime.
2. Local-81's packaged runtime/config contract still needs a final decision, specifically whether packaged runs should default to `/var/lib/local81` or remain repo-local.

## Next hardening steps

- add a tested config lookup strategy for `/etc/local81` and `/var/lib/local81`
- decide whether to vendor wheels vs. build online in the RPM buildroot
- add CI that runs `rpmbuild -ba` in a clean Rocky/RHEL-compatible builder
