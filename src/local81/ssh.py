"""SSH connection options for reaching target hosts.

Local-81 leans on the operator's own ``ssh``/``rsync`` and ``~/.ssh/config`` by
design. This module carries the few knobs an operator may want to pin in the
``[ssh]`` config section — user, port, identity file, host-key policy — and
renders them as the ssh argv flags and the rsync ``-e`` transport string.

Every field is optional. When all are empty the rendered output is empty, so
the default behaviour stays byte-identical to plain ``ssh host`` /
``rsync ... host:path`` and existing setups are unaffected.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

# Accepted values for OpenSSH StrictHostKeyChecking. ``accept-new`` (TOFU) is
# the sane onboarding default: trust on first connect, fail on key change.
STRICT_HOST_KEY_CHOICES = {"yes", "no", "accept-new", "ask"}


def _opt_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


@dataclass(frozen=True, slots=True)
class SshOptions:
    user: str | None = None
    port: int | None = None
    identity_file: str | None = None
    known_hosts: str | None = None
    strict_host_key_checking: str | None = None
    connect_timeout: int | None = None

    @classmethod
    def from_config(cls, data: dict | None) -> "SshOptions":
        data = data or {}
        return cls(
            user=(data.get("user") or None),
            port=_opt_int(data.get("port")),
            identity_file=(data.get("identity_file") or None),
            known_hosts=(data.get("known_hosts") or None),
            strict_host_key_checking=(data.get("strict_host_key_checking") or None),
            connect_timeout=_opt_int(data.get("connect_timeout")),
        )

    def ssh_args(self) -> list[str]:
        """Flags to insert into an ``ssh`` argv, before the host."""
        args: list[str] = []
        if self.identity_file:
            # IdentitiesOnly stops the agent from offering unrelated keys.
            args += ["-i", self.identity_file, "-o", "IdentitiesOnly=yes"]
        if self.port:
            args += ["-p", str(self.port)]
        if self.strict_host_key_checking:
            args += ["-o", f"StrictHostKeyChecking={self.strict_host_key_checking}"]
        if self.known_hosts:
            args += ["-o", f"UserKnownHostsFile={self.known_hosts}"]
        if self.connect_timeout:
            args += ["-o", f"ConnectTimeout={self.connect_timeout}"]
        return args

    def rsync_transport(self) -> list[str]:
        """``["-e", "ssh ..."]`` for rsync, or ``[]`` when no tweaks are needed."""
        args = self.ssh_args()
        if not args:
            return []
        return ["-e", "ssh " + " ".join(shlex.quote(a) for a in args)]

    def host_for(self, host: str) -> str:
        """Prefix the configured user, unless the host already carries one."""
        if self.user and "@" not in host:
            return f"{self.user}@{host}"
        return host
