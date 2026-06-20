"""Declarative recipe catalogs — the single source of truth for a fleet.

A *recipe catalog* describes a fleet once, declaratively (roles, the actions an
operator can run, and the menus those actions appear in), and is then
*projected* by the ``local81.ui`` renderers into everything an operator-facing
surface needs: Semaphore survey dropdowns + task templates, n8n workflows, and
the docker build/test matrix. There is exactly one place to edit a host, a
port, an action, or a menu option — this catalog — and every surface re-renders
from it.

Nothing in this package executes anything or contacts a host; it only loads and
validates the catalog and exposes typed accessors. Keeping it inert is what lets
the renderers stay pure (``local81.ui`` never touches the network either).
"""

from .catalog import (
    ActionCategory,
    ActionOption,
    Catalog,
    CatalogError,
    Parameter,
    Recipe,
    ServerGroup,
    SCHEMA_VERSION,
    load_catalog,
)

__all__ = [
    "ActionCategory",
    "ActionOption",
    "Catalog",
    "CatalogError",
    "Parameter",
    "Recipe",
    "ServerGroup",
    "SCHEMA_VERSION",
    "load_catalog",
]
