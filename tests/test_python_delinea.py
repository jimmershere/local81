from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from local81.secrets import (
    DelineaClient,
    SecretForbiddenError,
    SecretNotFoundError,
    SecretResolver,
    parse_ref,
)
from local81.secrets.errors import SecretRefSyntaxError


# --- ref parsing -----------------------------------------------------------

def test_parse_delinea_ref() -> None:
    ref = parse_ref("delinea://42#password")
    assert ref.scheme == "delinea"
    assert ref.location == "42"
    assert ref.key == "password"


def test_parse_delinea_requires_field_fragment() -> None:
    with pytest.raises(SecretRefSyntaxError):
        parse_ref("delinea://42")


# --- client over a mock Secret Server --------------------------------------

class _MockDelineaHandler(BaseHTTPRequestHandler):
    secrets = {"42": {"items": [
        {"slug": "username", "fieldName": "Username", "itemValue": "svc"},
        {"slug": "password", "fieldName": "Password", "itemValue": "delinea-pw"},
    ]}}

    def log_message(self, *_args) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if not self.path.endswith("/oauth2/token"):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        if "username=admin" in body and "password=correct" in body:
            payload = json.dumps({"access_token": "tok-123", "token_type": "bearer"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(401)
            self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.headers.get("Authorization") != "Bearer tok-123":
            self.send_response(401)
            self.end_headers()
            return
        secret_id = self.path.rsplit("/", 1)[-1]
        if secret_id not in self.secrets:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(self.secrets[secret_id]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def mock_delinea():
    server = HTTPServer(("127.0.0.1", 0), _MockDelineaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}"
    server.shutdown()
    thread.join()


def test_client_reads_secret_field(mock_delinea: str) -> None:
    client = DelineaClient(base_url=mock_delinea, token="tok-123")
    assert client.read_secret("42", "password") == "delinea-pw"
    # field matching is case-insensitive against slug/fieldName
    assert client.read_secret("42", "Username") == "svc"


def test_client_missing_field_not_found(mock_delinea: str) -> None:
    client = DelineaClient(base_url=mock_delinea, token="tok-123")
    with pytest.raises(SecretNotFoundError):
        client.read_secret("42", "nope")


def test_client_missing_secret_not_found(mock_delinea: str) -> None:
    client = DelineaClient(base_url=mock_delinea, token="tok-123")
    with pytest.raises(SecretNotFoundError):
        client.read_secret("999", "password")


def test_client_bad_token_forbidden(mock_delinea: str) -> None:
    client = DelineaClient(base_url=mock_delinea, token="wrong")
    with pytest.raises(SecretForbiddenError):
        client.read_secret("42", "password")


def test_password_grant_then_read(mock_delinea: str, monkeypatch) -> None:
    monkeypatch.setenv("DELINEA_BASE_URL", mock_delinea)
    monkeypatch.setenv("DELINEA_USERNAME", "admin")
    monkeypatch.setenv("DELINEA_PASSWORD", "correct")
    monkeypatch.delenv("DELINEA_TOKEN", raising=False)
    client = DelineaClient.from_env()
    assert client.token == "tok-123"
    assert client.read_secret("42", "password") == "delinea-pw"


def test_resolver_dispatches_delinea(mock_delinea: str) -> None:
    resolver = SecretResolver(
        delinea_client_factory=lambda: DelineaClient(base_url=mock_delinea, token="tok-123")
    )
    assert resolver.resolve("delinea://42#password") == "delinea-pw"
    # the resolved value is now scrubbable
    assert resolver.scrub("connecting with delinea-pw") == "connecting with ***"
