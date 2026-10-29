from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import Any

import yaml

from .models import DatabaseTarget
from .redaction import is_secret_key

_DATABASE_SECTION_RE = re.compile(r'^database "([^"]+)"$')
SUPPORTED_ENGINES = {"oracle19c", "postgres17", "sqlite"}
ALLOWED_DATABASE_KEYS = {
    "engine",
    "enabled",
    "tags",
    "host",
    "port",
    "database",
    "dbname",
    "service",
    "service_name",
    "sid",
    "instance",
    "path",
    "user",
    "user_env",
    "username_env",
    "password_env",
    "password_ref",
    "password_file",
    "wallet_password_env",
    "wallet",
    "wallet_dir",
    "connect_env",
    "connect_string_env",
    "dsn_env",
    "ssh_host",
    "role",
    "role_env",
    "backup_tool",
    "backup_profile",
    "monitoring_tool",
    "monitoring_profile",
    "audit_profile",
    "retention_days",
    "artifact_retention_days",
    "artifact_dir",
    "artifact_prefix",
    "tools",
    "notes",
    "service_ref",
    "socket",
    "sslmode",
    "ssl_mode",
    "ssl_root_cert",
    "connect_timeout",
    "barman_server",
    "pgbackrest_stanza",
    "log_path_hint",
}


class DatabaseConfigError(ValueError):
    pass


def _ref_syntax_errors(name: str, values: dict[str, Any]) -> list[str]:
    """Validate any ``*_ref`` value that is a managed ``scheme://`` reference.

    Plain ``*_ref`` values (e.g. an external service name) pass untouched; only
    values that look like a Local-81 secret reference are syntax-checked, so a
    typo in a ``bao://``/``sops://``/``env://`` ref fails fast at config load
    rather than at apply time.
    """
    from local81.secrets import SecretRefSyntaxError, is_secret_ref, parse_ref

    errors: list[str] = []
    for key, value in values.items():
        if key.endswith("_ref") and isinstance(value, str) and is_secret_ref(value):
            try:
                parse_ref(value)
            except SecretRefSyntaxError as exc:
                errors.append(f"database {name!r} key {key!r}: {exc}")
    return errors


def _parse_bool(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _normalize_target(name: str, values: dict[str, Any]) -> DatabaseTarget:
    engine = str(values.get("engine", "")).strip().lower()
    if engine not in SUPPORTED_ENGINES:
        raise DatabaseConfigError(f"database {name!r} engine must be one of {', '.join(sorted(SUPPORTED_ENGINES))}")
    for key in values:
        if is_secret_key(key):
            raise DatabaseConfigError(f"database {name!r} must not store literal secret key {key!r}; use *_env, *_ref, or *_file")
    for message in _ref_syntax_errors(name, values):
        raise DatabaseConfigError(message)
    settings = {key: value for key, value in values.items() if key not in {"engine", "enabled", "tags"}}
    return DatabaseTarget(
        name=name,
        engine=engine,
        enabled=_parse_bool(values.get("enabled"), default=True),
        tags=_parse_list(values.get("tags")),
        settings=settings,
    )


def _load_ini_targets(path: Path) -> list[DatabaseTarget]:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        raise DatabaseConfigError(f"could not parse {path}: {exc}") from exc
    targets: list[DatabaseTarget] = []
    for section in parser.sections():
        match = _DATABASE_SECTION_RE.match(section)
        if not match:
            continue
        name = match.group(1).strip()
        if not name:
            raise DatabaseConfigError("database section name must not be empty")
        targets.append(_normalize_target(name, dict(parser.items(section))))
    return targets


def _load_yaml_targets(path: Path) -> list[DatabaseTarget]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise DatabaseConfigError("top-level config must be a mapping")
    raw = data.get("databases") or {}
    targets: list[DatabaseTarget] = []
    if isinstance(raw, dict):
        for name, values in raw.items():
            if not isinstance(values, dict):
                raise DatabaseConfigError(f"database {name!r} must be a mapping")
            targets.append(_normalize_target(str(name), values))
        return targets
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict) or "name" not in item:
                raise DatabaseConfigError("database list entries must be mappings with a name")
            values = dict(item)
            name = str(values.pop("name"))
            targets.append(_normalize_target(name, values))
        return targets
    raise DatabaseConfigError("databases must be a mapping or list")


def load_database_targets(path: str | Path | None = None) -> list[DatabaseTarget]:
    from local81.config import DEFAULT_CONFIG_PATH, resolve_config_path

    config_path = resolve_config_path(path or DEFAULT_CONFIG_PATH)
    if config_path.suffix in {".yaml", ".yml"}:
        return _load_yaml_targets(config_path)
    return _load_ini_targets(config_path)


def validate_database_ini_section(section: str, options: set[str]) -> list[str]:
    match = _DATABASE_SECTION_RE.match(section)
    if not match:
        return []
    errors: list[str] = []
    if not match.group(1).strip():
        errors.append("database section name must not be empty")
    unknown = sorted(options - ALLOWED_DATABASE_KEYS)
    for key in unknown:
        errors.append(f"unknown key [{section}] {key}")
    if "engine" not in options:
        errors.append(f"missing required key [{section}] engine")
    for key in sorted(options):
        if is_secret_key(key):
            errors.append(f"[{section}] {key} must not contain a literal secret; use *_env, *_ref, or *_file")
    return errors


def validate_database_yaml_targets(data: dict[str, Any]) -> list[str]:
    raw = data.get("databases")
    if raw is None:
        return []
    errors: list[str] = []
    entries: list[tuple[str, dict[str, Any]]] = []
    if isinstance(raw, dict):
        for name, values in raw.items():
            if isinstance(values, dict):
                entries.append((str(name), values))
            else:
                errors.append(f"database {name!r} must be a mapping")
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and "name" in item:
                values = dict(item)
                name = str(values.pop("name"))
                entries.append((name, values))
            else:
                errors.append("database list entries must be mappings with a name")
    else:
        errors.append("databases must be a mapping or list")
    for name, values in entries:
        options = set(values)
        unknown = sorted(options - ALLOWED_DATABASE_KEYS)
        for key in unknown:
            errors.append(f"unknown database key {name}.{key}")
        engine = str(values.get("engine", "")).strip().lower()
        if engine not in SUPPORTED_ENGINES:
            errors.append(f"database {name!r} engine must be one of {', '.join(sorted(SUPPORTED_ENGINES))}")
        for key in sorted(options):
            if is_secret_key(key):
                errors.append(f"database {name!r} key {key!r} must not contain a literal secret; use *_env, *_ref, or *_file")
        errors.extend(_ref_syntax_errors(name, values))
    return errors
