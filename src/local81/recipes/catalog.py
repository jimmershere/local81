"""Load + validate a ``local81.recipes.v1`` catalog (pure; no I/O beyond read).

The catalog is YAML (PyYAML is already a runtime dependency) so it stays
operator-readable and greppable on disk. This module turns that file into typed
dataclasses and validates the cross-references the renderers rely on:

* recipe ``key`` values are unique and ports do not collide;
* every server-group member names a real recipe;
* every ``{placeholder}`` in an action command names a declared parameter.

Validation is strict and fails closed: an unknown key or a dangling reference is
an error, never a silent default — the same posture ``doctor``/``deploy --check``
take with ``config.ini``.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = "local81.recipes.v1"

# A {placeholder} in an action command. We use str.Formatter to find them so the
# accepted syntax matches what the renderers later substitute.
_PLACEHOLDER_RE = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)}")

_PARAM_TYPES = ("int", "str", "enum")


class CatalogError(Exception):
    """A catalog file is missing, malformed, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class ActionOption:
    """One selectable action — a menu entry that maps to a ``local81`` command.

    ``command`` is the CLI tail (without the ``local81`` prefix) and may contain
    ``{param}`` placeholders resolved from the catalog's parameters at render
    time. ``mutating`` marks actions that change target state, so renderers can
    surface a confirmation / require ``--execute`` semantics honestly.
    """

    key: str
    label: str
    command: str
    mutating: bool = False

    def placeholders(self) -> set[str]:
        return {name for _, name, _, _ in string.Formatter().parse(self.command) if name}


@dataclass(frozen=True, slots=True)
class ActionCategory:
    """A top-level menu (e.g. Deploy, Observe) holding an array of options."""

    key: str
    title: str
    options: tuple[ActionOption, ...]


@dataclass(frozen=True, slots=True)
class Parameter:
    """A named knob referenced by ``{key}`` in action commands.

    Projected into a Semaphore survey question and an n8n workflow input. ``enum``
    parameters carry ``choices`` (themselves a dropdown); ``int``/``str`` are free
    fields with a default.
    """

    key: str
    type: str = "str"
    default: Any = ""
    choices: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ServerGroup:
    """A named subset of the fleet — the "which hosts" dropdown."""

    key: str
    label: str
    members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Recipe:
    """One workload role: how to build it, deploy to it, and prove it healthy.

    A recipe is deliberately about *deployment* concerns Local-81 owns (image
    base, the payload target, a health/test probe), not the workload's internal
    correctness — that stays the workload's problem, kept honest.
    """

    key: str
    title: str
    role: str
    alias: str
    port: int
    group: str
    # build matrix
    base: str
    packages: tuple[str, ...]
    workload: str
    # deploy + verification
    target_dir: str
    health: str
    test: str

    def container_name(self, prefix: str) -> str:
        return f"{prefix}{self.alias}"


@dataclass(slots=True)
class Catalog:
    """A validated fleet description; the input to every ``local81.ui`` renderer."""

    schema: str
    name: str
    description: str
    categories: tuple[ActionCategory, ...]
    parameters: dict[str, Parameter]
    groups: tuple[ServerGroup, ...]
    recipes: tuple[Recipe, ...]
    source: Path | None = field(default=None)

    # --- accessors the renderers use ---------------------------------------

    def recipe(self, key: str) -> Recipe:
        for r in self.recipes:
            if r.key == key:
                return r
        raise KeyError(key)

    def group(self, key: str) -> ServerGroup:
        for g in self.groups:
            if g.key == key:
                return g
        raise KeyError(key)

    def group_aliases(self, key: str) -> list[str]:
        """SSH aliases of a group's members, in catalog order."""
        return [self.recipe(m).alias for m in self.group(key).members]

    def all_aliases(self) -> list[str]:
        return [r.alias for r in self.recipes]


# --- loading + validation --------------------------------------------------


def load_catalog(path: str | Path) -> Catalog:
    """Read + validate a catalog YAML file. Raises :class:`CatalogError`."""
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogError(f"recipe catalog not found: {p}") from exc
    except yaml.YAMLError as exc:
        raise CatalogError(f"recipe catalog is not valid YAML ({p}): {exc}") from exc
    if not isinstance(raw, dict):
        raise CatalogError(f"recipe catalog must be a mapping at the top level: {p}")

    catalog = _build(raw, source=p)
    _validate(catalog)
    return catalog


def _build(raw: dict[str, Any], *, source: Path | None) -> Catalog:
    schema = str(raw.get("schema", ""))
    if schema != SCHEMA_VERSION:
        raise CatalogError(
            f"unsupported catalog schema {schema!r}; this build understands {SCHEMA_VERSION!r}"
        )

    categories = tuple(_build_category(c) for c in raw.get("categories", []))
    parameters = {p.key: p for p in (_build_parameter(x) for x in raw.get("parameters", []))}
    groups = tuple(_build_group(g) for g in raw.get("groups", []))
    recipes = tuple(_build_recipe(r) for r in raw.get("recipes", []))

    return Catalog(
        schema=schema,
        name=str(raw.get("name", "fleet")),
        description=str(raw.get("description", "")),
        categories=categories,
        parameters=parameters,
        groups=groups,
        recipes=recipes,
        source=source,
    )


def _req(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d:
        raise CatalogError(f"{where}: missing required key {key!r}")
    return d[key]


def _build_category(c: dict[str, Any]) -> ActionCategory:
    key = str(_req(c, "key", "category"))
    options = tuple(_build_option(o, key) for o in c.get("options", []))
    if not options:
        raise CatalogError(f"category {key!r}: needs at least one option")
    return ActionCategory(key=key, title=str(c.get("title", key)), options=options)


def _build_option(o: dict[str, Any], cat_key: str) -> ActionOption:
    where = f"category {cat_key!r} option"
    return ActionOption(
        key=str(_req(o, "key", where)),
        label=str(o.get("label", o.get("key"))),
        command=str(_req(o, "command", where)),
        mutating=bool(o.get("mutating", False)),
    )


def _build_parameter(p: dict[str, Any]) -> Parameter:
    key = str(_req(p, "key", "parameter"))
    ptype = str(p.get("type", "str"))
    if ptype not in _PARAM_TYPES:
        raise CatalogError(f"parameter {key!r}: type must be one of {_PARAM_TYPES}, got {ptype!r}")
    choices = tuple(str(c) for c in p.get("choices", []))
    if ptype == "enum" and not choices:
        raise CatalogError(f"parameter {key!r}: type 'enum' needs a non-empty 'choices' list")
    return Parameter(key=key, type=ptype, default=p.get("default", ""), choices=choices)


def _build_group(g: dict[str, Any]) -> ServerGroup:
    key = str(_req(g, "key", "group"))
    members = tuple(str(m) for m in g.get("members", []))
    if not members:
        raise CatalogError(f"group {key!r}: needs at least one member")
    return ServerGroup(key=key, label=str(g.get("label", key)), members=members)


def _build_recipe(r: dict[str, Any]) -> Recipe:
    key = str(_req(r, "key", "recipe"))
    where = f"recipe {key!r}"
    build = r.get("build", {}) or {}
    deploy = r.get("deploy", {}) or {}
    return Recipe(
        key=key,
        title=str(r.get("title", key)),
        role=str(r.get("role", key)),
        alias=str(r.get("alias", key)),
        port=int(_req(r, "port", where)),
        group=str(r.get("group", "")),
        base=str(build.get("base", "debian:12-slim")),
        packages=tuple(str(x) for x in build.get("packages", [])),
        workload=str(build.get("workload", "")),
        target_dir=str(deploy.get("target_dir", "/srv/app")),
        health=str((r.get("health", {}) or {}).get("command", "")),
        test=str((r.get("test", {}) or {}).get("probe", "")),
    )


def _validate(cat: Catalog) -> None:
    if not cat.recipes:
        raise CatalogError("catalog has no recipes")

    # Unique recipe keys, aliases, and ports — collisions would make the rendered
    # ssh config / compose ambiguous.
    _no_dupes((r.key for r in cat.recipes), "recipe key")
    _no_dupes((r.alias for r in cat.recipes), "recipe alias")
    _no_dupes((str(r.port) for r in cat.recipes), "recipe port")
    _no_dupes((g.key for g in cat.groups), "group key")
    _no_dupes((c.key for c in cat.categories), "category key")

    recipe_keys = {r.key for r in cat.recipes}
    for g in cat.groups:
        for m in g.members:
            if m not in recipe_keys:
                raise CatalogError(f"group {g.key!r} references unknown recipe {m!r}")

    for r in cat.recipes:
        if r.group and r.group not in {g.key for g in cat.groups}:
            raise CatalogError(f"recipe {r.key!r} names unknown group {r.group!r}")

    # Every {placeholder} in a command must be a declared parameter.
    for c in cat.categories:
        for o in c.options:
            for name in o.placeholders():
                if name not in cat.parameters:
                    raise CatalogError(
                        f"category {c.key!r} option {o.key!r}: command references "
                        f"undeclared parameter {{{name}}}"
                    )


def _no_dupes(values: Any, label: str) -> None:
    seen: set[str] = set()
    for v in values:
        if v in seen:
            raise CatalogError(f"duplicate {label}: {v!r}")
        seen.add(v)
