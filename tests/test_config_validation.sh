#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

write_valid_config() {
  local path="$1"
  cat > "$path" <<'CFG'
[local81]
version = 0.1
project = validation-test
default_scope = web
state_dir = .local81/state
plans_dir = .local81/plans
runs_dir = .local81/runs
logs_dir = .local81/logs
lock_file = .local81/local81.lock
require_plan_for_deploy = true
fail_fast = true
max_parallel = 4
shell = /usr/bin/bash

[tools]
ssh = /usr/bin/ssh
rsync = /usr/bin/rsync
find = /usr/bin/find

[defaults]
rsync_opts = -az
backup = true
backup_suffix = .bkp
remote_mkdir = true
dry_run_default = false
log_hosts =
log_dest_dir = .local81/pulled-logs
jboss_log_path =
apache_log_path =

[routing]
env_from_filename_prefix = s:sys,q:qa,p:production
env_from_server_name_char_at = 4
env_from_server_name_char_map = s:sys,q:qa,p:production

[access]
allowed_users =
allowed_groups =
denied_users =
deny_root = false
allow_remote_cmd = false

[access.providers]
enabled = ldap,sudo,service_accounts,external
fail_closed = true

[access.ldap]
enabled = true
uri = ldaps://ad.example.com
bind_dn = CN=local81-reader,OU=Service Accounts,DC=example,DC=com
bind_password_env = LOCAL81_LDAP_BIND_PASSWORD
user_base_dn = OU=Users,DC=example,DC=com
group_base_dn = OU=Groups,DC=example,DC=com
user_filter = (sAMAccountName={user})
group_filter = (member={user_dn})
allowed_groups = Local81-Deployers
admin_groups = Local81-Admins
cache_ttl_seconds = 300
require_tls = true
fail_closed = true

[access.sudo]
enabled = true
require_sudo_user = true
allowed_sudo_users = deploybot
preserve_original_user = true
fail_closed = true

[access.service_accounts]
enabled = true
allowed_accounts = local81-ci
require_interactive_user = false
allowed_sources = ci,systemd,timer
fail_closed = true

[access.external]
enabled = true
command = /usr/local/bin/local81-policy-check
timeout_seconds = 5
input_format = json
fail_closed = true

[scope "web"]
enabled = true
source_dir = /srv/source
target_dir = /srv/target
servers = app1,app2
discovery = mtime_since_last_success
CFG
}

write_plan() {
  local path="$1"
  cat > "$path" <<'JSON'
{
  "schema": "local81.plan.v0.1",
  "kind": "plan",
  "mode": "deploy",
  "plan_id": "config-validation",
  "scopes": [
    {
      "scope": "web",
      "steps": [
        {
          "id": "scope:web:0001",
          "type": "rsync",
          "host": "app1",
          "cmd": "rsync -az -- \"/tmp/a\" \"app1:/srv/target/a\""
        }
      ]
    }
  ]
}
JSON
}

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
mkdir -p "$tmpdir/.local81/plans" "$tmpdir/.local81/runs" "$tmpdir/.local81/state"
write_valid_config "$tmpdir/.local81/config.ini"
write_plan "$tmpdir/.local81/plans/valid.plan.json"

(
  cd "$tmpdir"
  "$repo_root/bin/local81" doctor >"$tmpdir/doctor-valid.out"
)
grep -q '\[PASS\] config:schema:' "$tmpdir/doctor-valid.out"
grep -q 'LDAP/AD provider is scaffolded only' "$tmpdir/doctor-valid.out"

(
  cd "$tmpdir"
  "$repo_root/bin/local81" deploy --plan "$tmpdir/.local81/plans/valid.plan.json" --check >"$tmpdir/check-valid.out"
)
grep -q 'Check passed' "$tmpdir/check-valid.out"

cp "$tmpdir/.local81/config.ini" "$tmpdir/.local81/config.ini.good"
python3 - <<'PY' "$tmpdir/.local81/config.ini"
import sys
path = sys.argv[1]
text = open(path, encoding="utf-8").read()
text = text.replace("project = validation-test", "project = validation-test\nunknown_key = nope")
open(path, "w", encoding="utf-8").write(text)
PY

set +e
(
  cd "$tmpdir"
  "$repo_root/bin/local81" doctor >"$tmpdir/doctor-bad-key.out" 2>"$tmpdir/doctor-bad-key.err"
)
bad_doctor_rc=$?
set -e
[ "$bad_doctor_rc" -ne 0 ]
grep -q 'unknown key \[local81\] unknown_key' "$tmpdir/doctor-bad-key.out"

set +e
(
  cd "$tmpdir"
  "$repo_root/bin/local81" deploy --plan "$tmpdir/.local81/plans/valid.plan.json" --check >"$tmpdir/check-bad-key.out" 2>"$tmpdir/check-bad-key.err"
)
bad_check_rc=$?
set -e
[ "$bad_check_rc" -ne 0 ]
grep -q 'unknown key \[local81\] unknown_key' "$tmpdir/check-bad-key.out"

mv "$tmpdir/.local81/config.ini.good" "$tmpdir/.local81/config.ini"
cat >> "$tmpdir/.local81/config.ini" <<'CFG'

[scope "web"]
enabled = true
source_dir = /dup
target_dir = /dup
servers = dup
discovery = mtime_since_last_success
CFG

set +e
(
  cd "$tmpdir"
  "$repo_root/bin/local81" doctor >"$tmpdir/doctor-duplicate.out" 2>"$tmpdir/doctor-duplicate.err"
)
duplicate_rc=$?
set -e
[ "$duplicate_rc" -ne 0 ]
grep -q 'could not parse' "$tmpdir/doctor-duplicate.out"

write_valid_config "$tmpdir/.local81/config.ini"
cat >> "$tmpdir/.local81/config.ini" <<'CFG'

[mystery]
foo = bar
CFG

set +e
(
  cd "$tmpdir"
  "$repo_root/bin/local81" doctor >"$tmpdir/doctor-unknown-section.out" 2>"$tmpdir/doctor-unknown-section.err"
)
unknown_section_rc=$?
set -e
[ "$unknown_section_rc" -ne 0 ]
grep -q 'unknown section \[mystery\]' "$tmpdir/doctor-unknown-section.out"

echo "test_config_validation.sh: ok"
