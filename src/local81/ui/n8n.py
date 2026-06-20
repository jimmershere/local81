"""Render importable n8n workflows from a recipe catalog.

One workflow per action category. Each is a webhook that shells into the *same*
dispatcher script the Semaphore templates use (``dispatch/<category>.sh``), so
the behaviour behind the button is defined once and reused by every surface —
the web UI, the automation, and a plain operator at a shell all run identical
``local81`` invocations.

Pure: builds dicts, writes nothing, contacts nothing.
"""

from __future__ import annotations

from typing import Any

from ..recipes import Catalog

# Where the dispatcher scripts land relative to the control node's project dir.
_DISPATCH_DIR = ".local81/ui/semaphore/dispatch"


def render_n8n(catalog: Catalog) -> dict[str, dict[str, Any]]:
    """Return ``{filename: workflow_json}`` — one workflow per category."""
    return {
        f"local81-{catalog.name}-{cat.key}.workflow.json": _workflow(catalog, cat)
        for cat in catalog.categories
    }


def _env_assignments(catalog: Catalog, cat) -> str:
    """`name={{ $json.body.name || default }}` for each var the category reads."""
    parts = [r'action={{ $json.body.action }}']
    if cat.key == "deploy":
        parts.append(r"hosts={{ $json.body.hosts || 'all' }}")
    used: list[str] = []
    for o in cat.options:
        for name in o.placeholders():
            if name not in used:
                used.append(name)
    for name in used:
        default = catalog.parameters[name].default
        parts.append(f"{name}={{{{ $json.body.{name} || '{default}' }}}}")
    return " ".join(parts)


def _workflow(catalog: Catalog, cat) -> dict[str, Any]:
    path = f"local81-{catalog.name}-{cat.key}"
    # The leading '=' marks the whole field as an n8n expression so the inline
    # {{ $json.body.* }} parts evaluate; literal text (cd ..., shell ${VARS})
    # passes through untouched.
    command = (
        "=cd ${LOCAL81_DIR:-/srv/local81} && "
        f"{_env_assignments(catalog, cat)} "
        f"bash {_DISPATCH_DIR}/{cat.key}.sh"
    )
    actions = ", ".join(o.key for o in cat.options)
    return {
        "name": f"Local-81 {catalog.name}: {cat.title}",
        "meta": {
            "description": (
                f"Webhook -> dispatcher for the '{cat.title}' category. POST "
                f"{{\"action\": \"<one of: {actions}>\", ...}}. Runs the same "
                f"dispatch/{cat.key}.sh that the Semaphore template uses, so "
                "local81 stays the engine and this workflow is just orchestration."
            ),
        },
        "nodes": [
            {
                "parameters": {"httpMethod": "POST", "path": path, "responseMode": "lastNode"},
                "name": "Trigger",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [240, 300],
            },
            {
                "parameters": {"command": command},
                "name": f"{cat.title} dispatcher",
                "type": "n8n-nodes-base.executeCommand",
                "typeVersion": 1,
                "position": [520, 300],
            },
        ],
        "connections": {
            "Trigger": {"main": [[{"node": f"{cat.title} dispatcher", "type": "main", "index": 0}]]},
        },
    }
