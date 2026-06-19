"""Parse secret reference strings into a structured :class:`SecretRef`.

Three schemes are understood:

* ``env://NAME`` — the value is the *contents* of the named environment
  variable on the control node.
* ``bao://<mount>/<path>#<key>`` — an OpenBao / Vault KV v2 secret. ``mount``
  is the KV mount point, ``path`` the secret path under it, and ``key`` the
  field within that secret.
* ``sops://<file>#<dotted.json.path>`` — a SOPS-encrypted file, decrypted on
  demand; the fragment is a dotted path walked through the decrypted JSON.
* ``delinea://<secret-id>#<field>`` — a Delinea Secret Server secret, fetched
  over its REST API; ``field`` matches an item slug/fieldName within the secret.

Anything without a ``scheme://`` prefix is treated as a *literal* and rejected:
Local-81 never stores secret literals, so a bare value here is an operator
mistake we surface immediately rather than leak.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import SecretRefSyntaxError

KNOWN_SCHEMES = ("env", "bao", "sops", "delinea")


@dataclass(frozen=True, slots=True)
class SecretRef:
    scheme: str
    # env: location="NAME", key=None
    # bao: location="<mount>/<path>", key="<field>"
    # sops: location="<file>", key="<dotted.path>"
    location: str
    key: str | None = None
    raw: str = ""

    def redacted(self) -> str:
        """A loggable form of the reference (never the resolved value)."""
        return self.raw or f"{self.scheme}://{self.location}" + (f"#{self.key}" if self.key else "")


def is_secret_ref(spec: str) -> bool:
    """True when ``spec`` looks like a ``scheme://`` reference we manage."""
    for scheme in KNOWN_SCHEMES:
        if spec.startswith(f"{scheme}://"):
            return True
    return False


def parse_ref(spec: str) -> SecretRef:
    if not isinstance(spec, str) or "://" not in spec:
        raise SecretRefSyntaxError(
            f"{spec!r} is not a secret reference; expected one of "
            f"{', '.join(s + '://...' for s in KNOWN_SCHEMES)}"
        )
    scheme, _, rest = spec.partition("://")
    scheme = scheme.lower()
    if scheme not in KNOWN_SCHEMES:
        raise SecretRefSyntaxError(f"unknown secret scheme {scheme!r} in {spec!r}")
    if not rest:
        raise SecretRefSyntaxError(f"empty reference body in {spec!r}")

    if scheme == "env":
        if "#" in rest:
            raise SecretRefSyntaxError(f"env:// reference takes no '#key' fragment: {spec!r}")
        return SecretRef(scheme="env", location=rest, key=None, raw=spec)

    # bao + sops + delinea all require a '#<key>' fragment.
    location, sep, key = rest.partition("#")
    if not sep or not key:
        fragment = {"bao": "#<key>", "sops": "#<dotted.path>", "delinea": "#<field>"}.get(scheme, "#<key>")
        raise SecretRefSyntaxError(f"{scheme}:// reference must end with {fragment}: {spec!r}")
    if not location:
        raise SecretRefSyntaxError(f"{scheme}:// reference has an empty path: {spec!r}")
    if scheme == "bao" and "/" not in location:
        raise SecretRefSyntaxError(
            f"bao:// reference must be <mount>/<path>#<key>, got {spec!r}"
        )
    return SecretRef(scheme=scheme, location=location, key=key, raw=spec)
