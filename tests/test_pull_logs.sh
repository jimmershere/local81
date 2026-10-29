#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

test_pull_logs_with_settings() {
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  cat > "${tmpdir}/settings.cfg" <<'CFG'
log_hosts = app1,app2
log_dest_dir = ./collected
jboss_log_path = /var/log/jboss/server.log
apache_log_path = /var/log/httpd/access.log
CFG

  mkdir -p "${tmpdir}/stubs"
  cat > "${tmpdir}/stubs/scp" <<'SCP'
#!/usr/bin/env bash
set -euo pipefail
dest="${@: -1}"
src="${@: -2:1}"
mkdir -p "$dest"
touch "$dest/$(echo "$src" | tr ':/' '__')"
SCP
  chmod +x "${tmpdir}/stubs/scp"

  (
    cd "$tmpdir"
    PATH="${tmpdir}/stubs:$PATH" "$repo_root/bin/local81" pull-logs --settings "${tmpdir}/settings.cfg" >"${tmpdir}/out.txt"
  )

  grep -q 'Pulled logs to ./collected (success=4, failed=0)' "${tmpdir}/out.txt"
  [ -f "${tmpdir}/collected/app1/jboss/app1__var_log_jboss_server.log" ]
  [ -f "${tmpdir}/collected/app1/apache/app1__var_log_httpd_access.log" ]
  [ -f "${tmpdir}/collected/app2/jboss/app2__var_log_jboss_server.log" ]
}

test_pull_logs_with_cli_overrides() {
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  mkdir -p "${tmpdir}/stubs"
  cat > "${tmpdir}/stubs/scp" <<'SCP'
#!/usr/bin/env bash
set -euo pipefail
dest="${@: -1}"
mkdir -p "$dest"
touch "$dest/pulled.log"
SCP
  chmod +x "${tmpdir}/stubs/scp"

  (
    cd "$tmpdir"
    PATH="${tmpdir}/stubs:$PATH" "$repo_root/bin/local81" pull-logs \
      --hosts "h1" \
      --dest "${tmpdir}/logs" \
      --jboss-path "/tmp/jboss.log" >"${tmpdir}/out.txt"
  )

  grep -q "Pulled logs to ${tmpdir}/logs (success=1, failed=0)" "${tmpdir}/out.txt"
  [ -f "${tmpdir}/logs/h1/jboss/pulled.log" ]
}

test_pull_logs_missing_paths_fails() {
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' RETURN

  set +e
  (
    cd "$tmpdir"
    "$repo_root/bin/local81" pull-logs --hosts "h1" >"${tmpdir}/out.txt" 2>"${tmpdir}/err.txt"
  )
  local rc=$?
  set -e
  [ "$rc" -ne 0 ]
  grep -q 'no log paths configured' "${tmpdir}/err.txt"
}

test_pull_logs_with_settings
test_pull_logs_with_cli_overrides
test_pull_logs_missing_paths_fails

echo "test_pull_logs.sh: PASS"
