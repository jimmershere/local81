#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEB_DIR="${ROOT_DIR}/packaging/deb"
BUILD_DIR="${DEB_DIR}/.debbuild"
PKG_ROOT="${BUILD_DIR}/root"
ARTIFACT_DIR="${BUILD_DIR}/artifacts"
APP_ROOT="${PKG_ROOT}/opt/local81"
APP_LIB="${APP_ROOT}/app"
APP_VENV="${APP_ROOT}/venv"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'missing required tool: %s\n' "$1" >&2
    exit 1
  }
}

need dpkg-deb
need python3.12
need rsync

VERSION="$(ROOT_DIR="${ROOT_DIR}" python3.12 - <<'PY'
from pathlib import Path
import os
import re

text = (Path(os.environ["ROOT_DIR"]) / "pyproject.toml").read_text(encoding="utf-8")
match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
if not match:
    raise SystemExit("could not read version from pyproject.toml")
print(match.group(1))
PY
)"
RELEASE="${LOCAL81_PACKAGE_RELEASE:-2}"
PACKAGE_FILE="local81_${VERSION}-${RELEASE}_all.deb"

rm -rf "${BUILD_DIR}"
mkdir -p "${APP_LIB}" "${APP_VENV}" "${ARTIFACT_DIR}"
mkdir -p \
  "${PKG_ROOT}/DEBIAN" \
  "${PKG_ROOT}/usr/bin" \
  "${PKG_ROOT}/etc/local81" \
  "${PKG_ROOT}/var/lib/local81" \
  "${PKG_ROOT}/usr/share/doc/local81"

rsync -a "${ROOT_DIR}/bin" "${ROOT_DIR}/docs" "${ROOT_DIR}/examples" "${ROOT_DIR}/src" "${APP_LIB}/"
install -m 0644 "${ROOT_DIR}/pyproject.toml" "${APP_LIB}/pyproject.toml"
install -m 0644 "${ROOT_DIR}/README.md" "${APP_LIB}/README.md"
install -m 0644 "${ROOT_DIR}/CONTRIBUTING.md" "${APP_LIB}/CONTRIBUTING.md"

python3.12 -m venv --copies "${APP_VENV}"
export PIP_CACHE_DIR="${BUILD_DIR}/pip-cache"
"${APP_VENV}/bin/python" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
"${APP_VENV}/bin/python" -m pip install --disable-pip-version-check "${APP_LIB}"
rm -rf "${APP_LIB}/build" "${APP_LIB}/src"/*.egg-info
grep -RIl "${PKG_ROOT}" "${APP_VENV}" | xargs -r sed -i "s#${PKG_ROOT}##g"

install -m 0755 "${ROOT_DIR}/packaging/common/local81-wrapper" "${PKG_ROOT}/usr/bin/local81"
install -m 0644 "${ROOT_DIR}/packaging/rpm/local81.ini" "${PKG_ROOT}/etc/local81/local81.ini.example"
install -m 0644 "${ROOT_DIR}/README.md" "${PKG_ROOT}/usr/share/doc/local81/README.md"
install -m 0644 "${DEB_DIR}/README.md" "${PKG_ROOT}/usr/share/doc/local81/README.debian"

find "${PKG_ROOT}" -type d -exec chmod 0755 {} +
find "${PKG_ROOT}" -type f -exec chmod 0644 {} +
chmod 0755 "${PKG_ROOT}/usr/bin/local81"
find "${APP_VENV}/bin" -type f -exec chmod 0755 {} +
find "${APP_VENV}/bin" -type l -exec chmod 0755 {} + 2>/dev/null || true

INSTALLED_SIZE="$(du -sk "${PKG_ROOT}" | awk '{print $1}')"
cat > "${PKG_ROOT}/DEBIAN/control" <<EOF_CONTROL
Package: local81
Version: ${VERSION}-${RELEASE}
Section: admin
Priority: optional
Architecture: all
Maintainer: Jimmer Kelley <jimmershere@gmail.com>
Homepage: https://github.com/jimmershere/local81
Vcs-Browser: https://github.com/jimmershere/local81
Vcs-Git: https://github.com/jimmershere/local81.git
Installed-Size: ${INSTALLED_SIZE}
Depends: bash, python3.12, python3.12-venv, openssh-client, rsync, findutils
Description: Local-81 operator-readable deploy and runbook control plane
 Local-81 is a Python-based deployment and operator control plane for
 generating plans, validating deploys, and running controlled workflow
 operations.
 .
 This package installs Local-81 as an application bundle under /opt/local81
 and exposes the local81 command through /usr/bin/local81.
EOF_CONTROL

dpkg-deb --root-owner-group --build "${PKG_ROOT}" "${ARTIFACT_DIR}/${PACKAGE_FILE}"

printf '\nArtifacts:\n'
find "${ARTIFACT_DIR}" -type f -name '*.deb' | sort
