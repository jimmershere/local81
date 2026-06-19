"""Privilege escalation (``sudo``) for deploy steps, honestly and safely.

Local-81's ops layer only ever *marks* a command as needing sudo; nothing
escalates silently. This module is the one place that turns that marker into an
actual ``sudo`` invocation, and only when the operator opted in by supplying a
``become_password_ref`` (a secret reference — never a literal).

The password is resolved through the per-run :class:`SecretResolver`, so it is
held only in memory and is automatically scrubbed from run.json / logs by the
same machinery that masks every other resolved secret. It is fed to ``sudo -S``
on stdin (never on the command line, never in an artifact).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Become:
    enabled: bool = False
    user: str | None = None          # target user; None => root
    password: str | None = None      # resolved plaintext, in-memory only

    def wrap(self, command: str) -> str:
        """Return ``command`` wrapped to run under ``sudo -S`` (or unchanged).

        ``-S`` reads the password from stdin; ``-p ''`` suppresses the prompt so
        nothing about the password echoes to captured output; ``-k`` ignores any
        cached timestamp so the behaviour is deterministic.
        """
        if not self.enabled:
            return command
        sudo = ["sudo", "-S", "-k", "-p", ""]
        if self.user:
            sudo += ["-u", self.user]
        # Run the original command through a shell so pipes/operators survive.
        return " ".join(shlex.quote(part) for part in sudo) + f" bash -c {shlex.quote(command)}"

    @property
    def stdin(self) -> str | None:
        """The bytes to feed sudo on stdin, or None when not escalating."""
        if self.enabled and self.password is not None:
            return self.password + "\n"
        return None


DISABLED = Become()


def resolve_become(resolver, *, password_ref: str | None, enabled: bool = False,
                   user: str | None = None) -> Become:
    """Build a :class:`Become` from config, resolving the password ref in memory.

    ``resolver`` is the per-run :class:`SecretResolver`; resolving here registers
    the value for scrubbing. A bare (non-reference) password is refused: secrets
    never at rest, not even in a plan.
    """
    if not enabled:
        return DISABLED
    password = None
    if password_ref:
        from .secrets import is_secret_ref
        from .secrets.errors import SecretRefSyntaxError

        if not is_secret_ref(password_ref):
            raise SecretRefSyntaxError(
                f"become_password_ref must be a secret reference (env://, delinea://, ...), "
                f"not a literal: {password_ref!r}"
            )
        password = resolver.resolve(password_ref)
    return Become(enabled=True, user=user, password=password)
