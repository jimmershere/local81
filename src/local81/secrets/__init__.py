"""Secret resolution for Local-81.

Public surface: parse a ``scheme://`` reference, resolve it through the matching
backend (env / OpenBao KV v2 / SOPS), and scrub resolved plaintext out of any
artifact before it lands on disk. Local-81 stores *references*, never literals;
this package is the only place plaintext exists, and only in memory.
"""

from __future__ import annotations

from .bao import BaoClient
from .delinea import DelineaClient
from .errors import (
    SecretBackendError,
    SecretError,
    SecretForbiddenError,
    SecretNotFoundError,
    SecretRefSyntaxError,
)
from .refs import KNOWN_SCHEMES, SecretRef, is_secret_ref, parse_ref
from .resolver import SecretResolver, validate_refs

__all__ = [
    "BaoClient",
    "DelineaClient",
    "KNOWN_SCHEMES",
    "SecretBackendError",
    "SecretError",
    "SecretForbiddenError",
    "SecretNotFoundError",
    "SecretRef",
    "SecretRefSyntaxError",
    "SecretResolver",
    "is_secret_ref",
    "parse_ref",
    "validate_refs",
]
