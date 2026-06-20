from __future__ import annotations

from pathlib import Path

import pytest

from local81.recipes import CatalogError, load_catalog

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "recipes" / "fleet10.yaml"


# --- the shipped example catalog -------------------------------------------

def test_example_catalog_loads() -> None:
    c = load_catalog(EXAMPLE)
    assert c.name == "fleet10"
    assert len(c.recipes) == 10
    assert len(c.categories) == 5
    assert len(c.groups) == 5
    assert c.all_aliases()[0] == "web"


def test_group_aliases_resolve_in_order() -> None:
    c = load_catalog(EXAMPLE)
    assert c.group_aliases("data_tier") == ["pgdb", "cache", "mq"]
    assert c.group_aliases("all") == c.all_aliases()


def test_ports_and_keys_are_unique() -> None:
    c = load_catalog(EXAMPLE)
    ports = [r.port for r in c.recipes]
    assert len(ports) == len(set(ports))


# --- validation fails closed ------------------------------------------------

def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cat.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_bad_schema_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, "schema: nope\nrecipes: []\n")
    with pytest.raises(CatalogError):
        load_catalog(p)


def test_duplicate_recipe_key_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema: local81.recipes.v1
recipes:
  - { key: a, port: 2201 }
  - { key: a, port: 2202 }
""")
    with pytest.raises(CatalogError, match="duplicate recipe key"):
        load_catalog(p)


def test_duplicate_port_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema: local81.recipes.v1
recipes:
  - { key: a, port: 2201 }
  - { key: b, port: 2201 }
""")
    with pytest.raises(CatalogError, match="duplicate recipe port"):
        load_catalog(p)


def test_group_member_must_exist(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema: local81.recipes.v1
recipes:
  - { key: a, port: 2201 }
groups:
  - { key: g, members: [a, ghost] }
""")
    with pytest.raises(CatalogError, match="unknown recipe 'ghost'"):
        load_catalog(p)


def test_undeclared_parameter_placeholder_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema: local81.recipes.v1
recipes:
  - { key: a, port: 2201 }
categories:
  - key: c
    options:
      - { key: o, command: "deploy --forks {forks}" }
""")
    with pytest.raises(CatalogError, match="undeclared parameter"):
        load_catalog(p)


def test_enum_parameter_needs_choices(tmp_path: Path) -> None:
    p = _write(tmp_path, """
schema: local81.recipes.v1
recipes:
  - { key: a, port: 2201 }
parameters:
  - { key: scope, type: enum }
""")
    with pytest.raises(CatalogError, match="needs a non-empty 'choices'"):
        load_catalog(p)


def test_missing_file_is_catalog_error(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="not found"):
        load_catalog(tmp_path / "does-not-exist.yaml")
