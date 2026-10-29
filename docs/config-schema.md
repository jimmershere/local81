# LOCAL-81 Config Schema v0.1

This document defines the canonical `.local81/config.ini` contract for LOCAL-81 v0.1.

## Parsing and validation rules

`local81 doctor` and `local81 deploy --check` run strict schema validation:

- Unknown sections are errors.
- Unknown keys in known sections are errors.
- Duplicate sections or duplicate keys are errors.
- Keys are case-sensitive and must match the documented names.
- Boolean values must be `true` or `false`.
- Integer values must be valid integers in the documented range.
- Paths and required strings must not be empty.
- `default_scope` must match a configured `[scope "NAME"]` section when scopes exist.
- Scope `discovery` must be `mtime_since_last_success` in v0.1.

The runtime loader remains compatibility-oriented for older configs, but production workflows should treat validator failures as blocking.

## Canonical structure

### `[local81]` (required)

| Key | Type | Required | Default | Notes |
|---|---|---:|---|---|
| `version` | string | yes | `0.1` | Must equal `0.1` for this schema version. |
| `project` | string | yes | none | Human-readable project identifier. |
| `default_scope` | string | yes | none | Must match one defined scope name when scopes exist. |
| `state_dir` | path | yes | `.local81/state` | State JSON directory. |
| `plans_dir` | path | yes | `.local81/plans` | Plan artifacts directory. |
| `runs_dir` | path | yes | `.local81/runs` | Run artifacts directory. |
| `logs_dir` | path | yes | `.local81/logs` | Log directory. |
| `lock_file` | path | yes | `.local81/local81.lock` | Global LOCAL-81 lock file. |
| `require_plan_for_deploy` | bool | yes | `true` | Enforces precomputed plans before deploy. |
| `fail_fast` | bool | yes | `true` | Stop on first error. |
| `max_parallel` | int | yes | `4` | Must be >= 1. |
| `shell` | path | yes | `/usr/bin/bash` | Execution shell. |

### `[tools]` (required)

| Key | Type | Required | Default | Notes |
|---|---|---:|---|---|
| `ssh` | path | yes | `/usr/bin/ssh` | SSH executable path. |
| `rsync` | path | yes | `/usr/bin/rsync` | rsync executable path. |
| `find` | path | yes | `/usr/bin/find` | find executable path. |
| `jq` | path | no | none | Include only when available. |

### `[defaults]` (required)

| Key | Type | Required | Default | Notes |
|---|---|---:|---|---|
| `rsync_opts` | string | yes | `-az` | Default rsync option string. |
| `backup` | bool | yes | `true` | Backup behavior default. |
| `backup_suffix` | string | yes | `.bkp` | Backup suffix default. |
| `remote_mkdir` | bool | yes | `true` | Ensure remote target directory exists. |
| `dry_run_default` | bool | yes | `false` | Dry run default behavior. |
| `log_hosts` | csv-string | yes | empty | Default host list for `local81 pull-logs`. |
| `log_dest_dir` | path | yes | `.local81/pulled-logs` | Local destination for pulled diagnostics. |
| `jboss_log_path` | path | yes | empty | Remote JBoss log path or glob for `pull-logs`. |
| `apache_log_path` | path | yes | empty | Remote Apache log path or glob for `pull-logs`. |

### `[routing]` (required)

| Key | Type | Required | Default | Notes |
|---|---|---:|---|---|
| `env_from_filename_prefix` | map-string | yes | `s:sys,q:qa,p:production` | Comma-separated `prefix:env` map. |
| `env_from_server_name_char_at` | int | yes | `4` | 1-based character index. |
| `env_from_server_name_char_map` | map-string | yes | `s:sys,q:qa,p:production` | Comma-separated `char:env` map. |

### `[access]` (recommended)

Access policy is evaluated by `local81 deploy`, `local81 doctor`, and `local81 compliance report`.

| Key | Type | Required | Default | Notes |
|---|---|---:|---|---|
| `allowed_users` | csv-string | no | empty | If set, only listed local usernames can deploy. |
| `allowed_groups` | csv-string | no | empty | If set, only members of at least one listed local group can deploy. |
| `denied_users` | csv-string | no | empty | Listed users are always denied. |
| `deny_root` | bool | no | `false` | When `true`, blocks deploy/compliance actor checks for UID 0. |
| `allow_remote_cmd` | bool | no | `true` | When `false`, deploy blocks selected plan steps with `type = remote_cmd`. |

Generated configs set `allow_remote_cmd = false` for least privilege. Runtime directories and generated run/state artifacts are always written owner-only by the runtime (`0700` directories and `0600` files). Existing configs without `[access]` continue to load, but `local81 doctor` and `local81 compliance report` warn that access policy is not configured.

### `[access.providers]` (optional)

Production authorization provider sections are scaffolded for future enterprise integrations. In v0.1 these sections are parsed, schema-validated, and reported by `doctor` / `compliance report`, but they do not make LDAP network calls, invoke sudo, execute external policy commands, or change deploy authorization decisions yet.

| Key | Type | Required | Default | Notes |
|---|---|---:|---|---|
| `enabled` | csv-string | no | empty | Provider names to enable: `ldap`, `sudo`, `service_accounts`, `external`. |
| `fail_closed` | bool | no | `true` | Intended future behavior when a configured provider cannot be evaluated. |

### `[access.ldap]` (optional scaffold)

| Key | Type | Required | Default | Notes |
|---|---|---:|---|---|
| `enabled` | bool | no | `false` | Enables LDAP/AD scaffold warnings. |
| `uri` | string | no | empty | Should use `ldaps://`. |
| `bind_dn` | string | no | empty | Directory bind DN. |
| `bind_password_env` | string | no | empty | Environment variable containing the bind password. Never store the password directly in config. |
| `user_base_dn` | string | no | empty | User search base. |
| `group_base_dn` | string | no | empty | Group search base. |
| `user_filter` | string | no | empty | User search filter template. |
| `group_filter` | string | no | empty | Group search filter template. |
| `allowed_groups` | csv-string | no | empty | Future deployer group mapping. |
| `admin_groups` | csv-string | no | empty | Future admin group mapping. |
| `cache_ttl_seconds` | int | no | `0` | Future lookup cache TTL; must be >= 0. |
| `require_tls` | bool | no | `true` | Future TLS enforcement setting. |
| `fail_closed` | bool | no | inherited | Future provider failure behavior. |

### `[access.sudo]` (optional scaffold)

| Key | Type | Required | Default | Notes |
|---|---|---:|---|---|
| `enabled` | bool | no | `false` | Enables sudo scaffold warnings. |
| `require_sudo_user` | bool | no | `false` | Future requirement for an original sudo actor. |
| `allowed_sudo_users` | csv-string | no | empty | Future sudo user allowlist. |
| `preserve_original_user` | bool | no | `true` | Future audit attribution behavior. |
| `fail_closed` | bool | no | inherited | Future provider failure behavior. |

### `[access.service_accounts]` (optional scaffold)

| Key | Type | Required | Default | Notes |
|---|---|---:|---|---|
| `enabled` | bool | no | `false` | Enables service-account scaffold warnings. |
| `allowed_accounts` | csv-string | no | empty | Future service account allowlist. |
| `require_interactive_user` | bool | no | `false` | Future non-interactive run guard. |
| `allowed_sources` | csv-string | no | empty | Future source classes such as `ci`, `systemd`, `timer`. |
| `fail_closed` | bool | no | inherited | Future provider failure behavior. |

### `[access.external]` (optional scaffold)

| Key | Type | Required | Default | Notes |
|---|---|---:|---|---|
| `enabled` | bool | no | `false` | Enables external policy scaffold warnings. |
| `command` | path | no | empty | Future JSON policy provider command. Not executed in v0.1. |
| `timeout_seconds` | int | no | `5` | Future provider timeout; must be >= 1. |
| `input_format` | string | no | `json` | Future provider input format. |
| `fail_closed` | bool | no | inherited | Future provider failure behavior. |

### `[scope "NAME"]` (0..N)

Each scope section name is literal form `[scope "<scope-name>"]`.

Required keys:

| Key | Type | Required | Default | Notes |
|---|---|---:|---|---|
| `enabled` | bool | yes | `true` | Scope activation flag. |
| `source_dir` | path | yes | none | Source path to deploy from. |
| `target_dir` | path | yes | none | Destination path to deploy to. |
| `servers` | csv-string | yes | none | Comma-separated host list; no spaces around commas. |
| `discovery` | enum | yes | `mtime_since_last_success` | Allowed: `mtime_since_last_success` in v0.1. |

Optional keys:

| Key | Type | Required | Default |
|---|---|---:|---|
| `rsync_opts` | string | no | inherited from `[defaults].rsync_opts` |
| `backup` | bool | no | inherited from `[defaults].backup` |
| `backup_suffix` | string | no | inherited from `[defaults].backup_suffix` |


### `[database "NAME"]` (0..N)

Database targets power `local81 db` commands. The section name is literal form `[database "<target-name>"]`.

| Key | Type | Required | Notes |
|---|---|---:|---|
| `engine` | enum | yes | `oracle19c`, `postgres17`, or `sqlite`. |
| `enabled` | bool | no | Defaults to `true`. |
| `tags` | csv-string | no | Operator labels. |
| `host` / `port` | string/int | no | Network locator for Oracle/PostgreSQL plans. |
| `database` | string | no | PostgreSQL database name or generic database label. |
| `service`, `service_name`, `sid` | string | no | Oracle service/SID locator. |
| `path` | path | no | SQLite database path. |
| `user`, `user_env`, `username_env` | string | no | User name or environment variable name. |
| `password_env`, `password_ref`, `password_file` | string | no | Secret environment variable/reference. Literal password keys are rejected. |
| `connect_env`, `dsn_env` | string | no | Environment variable containing a connection descriptor. |
| `ssh_host` | string | no | Optional host used for remote execution wrappers. |
| `backup_tool`, `backup_profile` | string | no | Preferred backup integration. |
| `monitoring_tool`, `monitoring_profile` | string | no | Preferred monitoring integration. |
| `audit_profile` | string | no | Audit/compliance profile label. |
| `retention_days`, `artifact_retention_days` | int | no | Retention hints. |
| `tools` | csv-string | no | Optional tool profile hints. |

Never store literal passwords, tokens, or keys in database target sections. Use `*_env`, `*_ref`, or `*_file` fields so Local-81 can plan commands without printing secret values.

### `[compliance]` (optional)

Compliance settings provide defaults for read-only `local81 compliance` commands. Command-line flags override these defaults. These settings do not cause Local-81 to mutate system files.

| Key | Type | Required | Notes |
|---|---|---:|---|
| `enabled` | bool | no | Enables a profile-level compliance intent marker. |
| `profile` | string | no | Label such as `nist-800-53r5`, `cms-ars-5.1`, or `cis-l1`. |
| `root` | path | no | Default root path for compliance scans. |
| `scope` | enum | no | `all`, `access`, `linux`, `os`, `web`, `java`, `javascript`, `node`, or `angular`. |
| `fail_on` | enum | no | `never`, `low`, `medium`, `high`, or `critical`. |
| `report_dir` | path | no | Default artifact directory for compliance reports and plans. |
| `controls` | csv-string | no | Control filters or labels such as `NIST_SP_800_53:AC-3,CMS_ARS:CM-6`. |
| `include_passed` | bool | no | Include passing findings in reports. |
| `strict_unknown` | bool | no | Intended CI policy marker for treating unknowns strictly. |
| `include_slow` | bool | no | Reserved for future explicitly slower local checks. |

### `[compliance "NAME"]` (0..N)

Named compliance targets can describe project- or host-specific scan roots and policy context.

| Key | Type | Required | Notes |
|---|---|---:|---|
| `enabled` | bool | no | Enables the named compliance target. |
| `profile` | string | no | Compliance profile label. |
| `root` | path | no | Scan root for this target. |
| `scope` | enum | no | Scanner scope for this target. |
| `fail_on` | enum | no | Failure threshold label. |
| `controls` | csv-string | no | Control filter labels. |
| `paths` | csv-string | no | Additional candidate paths. |
| `owner` | string | no | Operational owner label. |
| `environment` | string | no | Environment label such as `prod` or `qa`. |
| `waivers` | csv-string | no | Waiver IDs or exception references; do not store secrets. |

### `[compliance.inventory]` and `[compliance.hardening]` (optional)

`[compliance.inventory]` accepts `include_sections` and `extra_paths`. `[compliance.hardening]` accepts `include_advisory`. Harden plans are advisory only and do not apply changes.

## Legacy import rules (`local81 init --import`)

Legacy `settings.cfg` import accepts:

- Section headers: `[NAME]`
- Assignments: `key=value` and `key = value`
- Leading/trailing whitespace around keys and values (trimmed)
- Blank lines and comment lines starting with `#` or `;` (ignored)

Legacy known keys:

- Required per section: `source_dir`, `target_dir`, `servers`
- Optional per section: `rsync_opts`, `backup`, `backup_suffix`
- Optional global keys (outside sections): `log_hosts`, `log_dest_dir`, `jboss_log_path`, `apache_log_path`

Unknown legacy keys are ignored with a warning (one warning per unknown key name).

Import fails if any legacy section is missing required keys.
