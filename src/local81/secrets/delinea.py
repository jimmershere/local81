"""Minimal Delinea Secret Server (formerly Thycotic) read client (stdlib only).

Implements just the read path local81 needs — OAuth2 token then a single
secret fetch — rather than pulling in the ``python-tss-sdk``: keeps the
stdlib-first guarantee and the attack surface tiny. TLS verification is on by
default and only relaxed by an explicit, loud env flag (``DELINEA_SKIP_VERIFY``).

Auth resolution order:
1. ``DELINEA_TOKEN`` — a pre-obtained bearer token (e.g. injected by CI).
2. ``DELINEA_USERNAME`` + ``DELINEA_PASSWORD`` — OAuth2 password grant against
   ``{base}/oauth2/token``.

The base URL comes from ``DELINEA_BASE_URL`` (or ``DELINEA_SECRET_SERVER_URL``),
e.g. ``https://secrets.example.com/SecretServer``.

Reference form: ``delinea://<secret-id>#<field>`` — ``field`` matches a secret
item's ``slug`` or ``fieldName`` (case-insensitive); its ``itemValue`` is
returned.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .errors import SecretBackendError, SecretForbiddenError, SecretNotFoundError

_TIMEOUT_SECONDS = 15.0


def _base_url() -> str:
    base = os.environ.get("DELINEA_BASE_URL") or os.environ.get("DELINEA_SECRET_SERVER_URL")
    if not base:
        raise SecretBackendError(
            "no Delinea base URL: set DELINEA_BASE_URL (e.g. https://host/SecretServer)"
        )
    return base.rstrip("/")


def _ssl_context() -> ssl.SSLContext | None:
    if os.environ.get("DELINEA_SKIP_VERIFY", "").lower() in {"1", "true", "yes"}:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None  # urllib uses its verifying default context


@dataclass(slots=True)
class DelineaClient:
    base_url: str
    token: str

    @classmethod
    def from_env(cls) -> DelineaClient:
        base = _base_url()
        token = os.environ.get("DELINEA_TOKEN")
        if not token:
            username = os.environ.get("DELINEA_USERNAME")
            password = os.environ.get("DELINEA_PASSWORD")
            if not (username and password):
                raise SecretBackendError(
                    "no Delinea credentials: set DELINEA_TOKEN, or "
                    "DELINEA_USERNAME + DELINEA_PASSWORD"
                )
            token = _password_grant(base, username, password)
        return cls(base_url=base, token=token)

    def read_secret(self, secret_id: str, field: str) -> str:
        url = f"{self.base_url}/api/v1/secrets/{urllib.parse.quote(secret_id, safe='')}"
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        body = _send(request, what=f"delinea secret {secret_id}")
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise SecretNotFoundError(f"delinea secret {secret_id} has no items payload")
        wanted = field.lower()
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("slug", "")).lower() == wanted or str(item.get("fieldName", "")).lower() == wanted:
                return str(item.get("itemValue", ""))
        raise SecretNotFoundError(f"delinea secret {secret_id} has no field {field!r}")


def _password_grant(base: str, username: str, password: str) -> str:
    url = f"{base}/oauth2/token"
    payload = urllib.parse.urlencode(
        {"grant_type": "password", "username": username, "password": password}
    ).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    body = _send(request, what="delinea oauth2 token")
    token = body.get("access_token") if isinstance(body, dict) else None
    if not token:
        raise SecretBackendError("delinea token endpoint returned no access_token")
    return str(token)


def _send(request: urllib.request.Request, *, what: str) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise SecretForbiddenError(f"{what}: access denied ({exc.code})") from exc
        if exc.code == 404:
            raise SecretNotFoundError(f"{what}: not found (404)") from exc
        raise SecretBackendError(f"{what}: request failed ({exc.code})") from exc
    except urllib.error.URLError as exc:
        raise SecretBackendError(f"{what}: unreachable ({exc.reason})") from exc
