"""Dispatch a :class:`SecretRef` to its backend and return the plaintext.

The resolver is the single choke point where secret plaintext enters the
process. It also doubles as a *scrubber registry*: every value it hands out is
remembered (by value, never logged) so that :meth:`SecretResolver.scrub` can
strip those exact strings out of command output, host logs, and run manifests
before they are written to disk. That is what backs the "secrets never at rest"
guarantee end to end.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from .bao import BaoClient
from .delinea import DelineaClient
from .errors import SecretNotFoundError
from .refs import SecretRef, is_secret_ref, parse_ref
from .sops import decrypt_value

_PLACEHOLDER = "***"


class SecretResolver:
    def __init__(
        self,
        *,
        bao_client_factory: Callable[[], BaoClient] | None = None,
        delinea_client_factory: Callable[[], DelineaClient] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._bao_client_factory = bao_client_factory or BaoClient.from_env
        self._delinea_client_factory = delinea_client_factory or DelineaClient.from_env
        self._env = env if env is not None else os.environ
        self._bao: BaoClient | None = None
        self._delinea: DelineaClient | None = None
        self._seen: set[str] = set()

    def resolve(self, spec: str | SecretRef) -> str:
        ref = spec if isinstance(spec, SecretRef) else parse_ref(spec)
        value = self._dispatch(ref)
        if value:
            self._seen.add(value)
        return value

    def resolve_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        """Resolve every ``scheme://`` value in ``mapping``; pass others through."""
        out: dict[str, Any] = {}
        for key, value in mapping.items():
            if isinstance(value, str) and is_secret_ref(value):
                out[key] = self.resolve(value)
            else:
                out[key] = value
        return out

    def _dispatch(self, ref: SecretRef) -> str:
        if ref.scheme == "env":
            try:
                return self._env[ref.location]
            except KeyError as exc:
                raise SecretNotFoundError(f"environment variable {ref.location!r} is not set") from exc
        if ref.scheme == "bao":
            mount, _, path = ref.location.partition("/")
            return self._bao_client().read_kv2(mount, path, ref.key or "")
        if ref.scheme == "sops":
            return decrypt_value(ref.location, ref.key or "")
        if ref.scheme == "delinea":
            return self._delinea_client().read_secret(ref.location, ref.key or "")
        raise SecretNotFoundError(f"no backend for scheme {ref.scheme!r}")

    def _bao_client(self) -> BaoClient:
        if self._bao is None:
            self._bao = self._bao_client_factory()
        return self._bao

    def _delinea_client(self) -> DelineaClient:
        if self._delinea is None:
            self._delinea = self._delinea_client_factory()
        return self._delinea

    @property
    def resolved_values(self) -> frozenset[str]:
        return frozenset(self._seen)

    def scrub(self, text: str) -> str:
        """Replace every resolved secret value seen so far with ``***``."""
        if not text:
            return text
        # Longest first so a value that contains another is masked wholesale.
        for value in sorted(self._seen, key=len, reverse=True):
            if value:
                text = text.replace(value, _PLACEHOLDER)
        return text


def validate_refs(specs: list[str]) -> list[str]:
    """Return syntax-error messages for any malformed refs (empty list == ok).

    Used at plan-validation time: a *syntactically* bad reference is a typo we
    can catch before touching any backend, failing fast and loud.
    """
    from .errors import SecretRefSyntaxError

    errors: list[str] = []
    for spec in specs:
        try:
            parse_ref(spec)
        except SecretRefSyntaxError as exc:
            errors.append(str(exc))
    return errors
