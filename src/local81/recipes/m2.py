"""M2 stack onboarding: classify a host by name, then discover its stack read-only.

The "M2" fleet is a JBoss/Java + Oracle/PostgreSQL + IBM MQ application stack
whose hosts follow a naming convention Local-81 can key off of:

* ``...m2in...``  -> a PostgreSQL 17 database host       (role ``m2-postgres``)
* ``...m2or...``  -> an Oracle database host             (role ``m2-oracle``)
* ``...m2tr...``  -> an IBM MQ queue-manager host        (role ``m2-mq``)
* ``...m2...``    -> a JBoss/Java application host        (role ``m2-app``)
  (web vhosts, the Java client under ``/app``, the Oracle client under ``/opt``,
  the ``engin/palmed`` and ``smartxfr`` payloads, JBoss ``standalone/`` config)

Everything here is **pure and read-only**. ``classify_host`` and
``discovery_checks`` are inert data; ``discovery_plan`` builds the argv to *probe*
a host (via a :class:`~local81.connectors.Connector`) and ``render_discovery_script``
emits an equivalent standalone read-only shell report. Nothing in this module
mutates a target or contacts the network — the same posture the rest of the
``recipes`` package takes.

The checks are deliberately about *where the stack lives* (paths, services,
packages, ``.xml``/``.properties`` config), not the application's internal
correctness — that stays the workload's problem, kept honest.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from ..facts.probes import (
    dir_state_probe,
    file_state_probe,
    package_state_probe,
    service_state_probe,
)

# --- roles -----------------------------------------------------------------

ROLE_POSTGRES = "m2-postgres"
ROLE_ORACLE = "m2-oracle"
ROLE_MQ = "m2-mq"
ROLE_APP = "m2-app"

# Marker substring -> role. Order matters: the specific data/queue roles win
# over the generic application role, so a host is matched against these in
# sequence and the first hit decides.
_ROLE_MARKERS: tuple[tuple[str, str], ...] = (
    ("m2in", ROLE_POSTGRES),
    ("m2or", ROLE_ORACLE),
    ("m2tr", ROLE_MQ),
    ("m2", ROLE_APP),
)

ROLE_GROUP = {
    ROLE_POSTGRES: ("m2_postgres", "M2 PostgreSQL 17 DB hosts (m2in*)"),
    ROLE_ORACLE: ("m2_oracle", "M2 Oracle DB hosts (m2or*)"),
    ROLE_MQ: ("m2_mq", "M2 IBM MQ queue-manager hosts (m2tr*)"),
    ROLE_APP: ("m2_app", "M2 JBoss/Java application hosts"),
}


class M2Error(Exception):
    """A host name carries no ``m2`` marker, or an unknown role was requested."""


# --- checks ----------------------------------------------------------------

# A discovery check is one read-only observation. ``kind`` selects how the probe
# argv (and the shell report line) are built:
#   dir / file       -> a coreutils stat/test on ``target``
#   service          -> systemctl is-active/is-enabled on ``target``
#   package          -> dpkg-query/rpm on ``target``
#   find             -> ``find <target> -name <pattern>`` (config-file sweep)
#   shell            -> a raw read-only ``sh -c`` snippet in ``target`` (lets us
#                       honour env vars like $JBOSS_HOME and OR-globs honestly)
@dataclass(frozen=True, slots=True)
class Check:
    key: str
    kind: str
    target: str
    description: str
    pattern: str = ""
    maxdepth: int = 4


_KINDS = ("dir", "file", "service", "package", "find", "shell")


# Shared across every M2 host: the Java client tree and the Oracle client root.
_BASE_CHECKS: tuple[Check, ...] = (
    Check("java_client", "dir", "/app", "Java client / application root (/app/*)"),
    Check("oracle_client", "dir", "/opt", "Oracle client / third-party install root (/opt)"),
    Check(
        "oracle_client_net",
        "find",
        "/opt",
        "Oracle client net config (tnsnames/sqlnet under /opt)",
        pattern="tnsnames.ora",
        maxdepth=6,
    ),
)

# JBoss/Java application hosts: web vhosts, the engin/smartxfr payloads, and the
# JBoss standalone configuration + /props convention.
_APP_CHECKS: tuple[Check, ...] = (
    Check("web_vhosts", "dir", "/var/www/html/vhosts", "Apache vhost document roots"),
    Check("engin_palmed", "dir", "/app/engin/palmed", "engin payload (/app/engin/palmed)"),
    Check("smartxfr", "dir", "/app/smartxfr", "smartxfer payload (/app/smartxfr)"),
    Check(
        "jboss_standalone",
        "shell",
        'h="${JBOSS_HOME:-/opt/jboss-eap}"; d="$h/standalone"; '
        '[ -d "$d" ] && echo "JBOSS_HOME=$h" && '
        'ls -1 "$d"/configuration/*.xml 2>/dev/null | head -50 || echo "JBOSS_STANDALONE absent ($d)"',
        "JBoss $JBOSS_HOME/standalone/ configuration (*.xml)",
    ),
    Check(
        "app_properties",
        "find",
        "/app",
        "Java app .properties config under /app",
        pattern="*.properties",
        maxdepth=5,
    ),
    Check(
        "props_dirs",
        "find",
        "/props",
        "Shared /props .properties config",
        pattern="*.properties",
        maxdepth=4,
    ),
    Check(
        "app_xml",
        "find",
        "/app",
        "Java/JBoss .xml config under /app",
        pattern="*.xml",
        maxdepth=5,
    ),
)

# PostgreSQL 17 DB hosts (m2in*).
_POSTGRES_CHECKS: tuple[Check, ...] = (
    Check("pg_pkg_deb", "package", "postgresql-17", "PostgreSQL 17 server package (Debian/Ubuntu name)"),
    Check("pg_pkg_rpm", "package", "postgresql17-server", "PostgreSQL 17 server package (RHEL name)"),
    Check("pg_service", "service", "postgresql-17", "PostgreSQL 17 systemd service"),
    Check(
        "pg_data",
        "shell",
        'for d in /var/lib/pgsql/17/data /var/lib/postgresql/17/main /pgdata; do '
        '[ -d "$d" ] && echo "PGDATA=$d"; done; true',
        "PostgreSQL 17 data directory",
    ),
    Check(
        "pg_config",
        "shell",
        'for f in /var/lib/pgsql/17/data/postgresql.conf /etc/postgresql/17/main/postgresql.conf '
        '/var/lib/pgsql/17/data/pg_hba.conf /etc/postgresql/17/main/pg_hba.conf; do '
        '[ -f "$f" ] && echo "PGCONF=$f"; done; true',
        "PostgreSQL 17 postgresql.conf / pg_hba.conf",
    ),
    Check(
        "pg_bin",
        "shell",
        'command -v psql >/dev/null 2>&1 && psql --version || echo "psql absent"',
        "PostgreSQL client binary + version",
    ),
)

# Oracle DB hosts (m2or*).
_ORACLE_CHECKS: tuple[Check, ...] = (
    Check("ora_oratab", "file", "/etc/oratab", "Oracle /etc/oratab (SID -> ORACLE_HOME map)"),
    Check(
        "ora_home",
        "shell",
        'for d in /opt/oracle /u01/app/oracle /opt/oracle/product; do '
        '[ -d "$d" ] && echo "ORACLE_HOME_CANDIDATE=$d"; done; true',
        "ORACLE_HOME candidate directories",
    ),
    Check(
        "ora_net",
        "shell",
        'find /opt/oracle /u01 -maxdepth 7 \\( -name listener.ora -o -name tnsnames.ora '
        '-o -name sqlnet.ora \\) 2>/dev/null | head -20; true',
        "Oracle net config (listener/tnsnames/sqlnet.ora)",
    ),
    Check(
        "ora_instances",
        "shell",
        'pgrep -fl ora_pmon 2>/dev/null | head -20 || echo "no ora_pmon process"',
        "Running Oracle instances (ora_pmon_<SID> processes)",
    ),
    Check(
        "ora_bin",
        "shell",
        'command -v sqlplus >/dev/null 2>&1 && (sqlplus -V 2>/dev/null | head -1) || echo "sqlplus absent"',
        "Oracle sqlplus binary + version",
    ),
)

# IBM MQ queue-manager hosts (m2tr*).
_MQ_CHECKS: tuple[Check, ...] = (
    Check("mq_install", "dir", "/opt/mqm", "IBM MQ install root (/opt/mqm)"),
    Check("mq_data", "dir", "/var/mqm", "IBM MQ data root (/var/mqm)"),
    Check(
        "mq_qmgrs",
        "shell",
        'command -v dspmq >/dev/null 2>&1 && dspmq 2>/dev/null || echo "dspmq absent"',
        "Configured queue managers (dspmq)",
    ),
    Check(
        "mq_qm_ini",
        "find",
        "/var/mqm/qmgrs",
        "Queue-manager qm.ini files",
        pattern="qm.ini",
        maxdepth=3,
    ),
    Check(
        "mq_mqsc",
        "find",
        "/var/mqm",
        "MQSC definition scripts (*.mqsc)",
        pattern="*.mqsc",
        maxdepth=4,
    ),
)

_ROLE_CHECKS: dict[str, tuple[Check, ...]] = {
    ROLE_POSTGRES: _POSTGRES_CHECKS,
    ROLE_ORACLE: _ORACLE_CHECKS,
    ROLE_MQ: _MQ_CHECKS,
    ROLE_APP: _APP_CHECKS,
}


def classify_host(name: str) -> str:
    """Return the M2 role for a host name, by its first marker substring.

    Fail-closed: a name with no ``m2`` marker raises :class:`M2Error` rather than
    guessing — the same posture ``doctor``/``deploy --check`` take with config.
    """
    lowered = name.strip().lower()
    if not lowered:
        raise M2Error("empty host name")
    for marker, role in _ROLE_MARKERS:
        if marker in lowered:
            return role
    raise M2Error(f"host {name!r} carries no 'm2' marker; not an M2 host")


def is_m2_host(name: str) -> bool:
    """True when ``name`` carries an ``m2`` marker (i.e. ``classify_host`` succeeds)."""
    try:
        classify_host(name)
        return True
    except M2Error:
        return False


def discovery_checks(role: str) -> tuple[Check, ...]:
    """The ordered, read-only checks for a role: the shared base plus role extras."""
    if role not in _ROLE_CHECKS:
        raise M2Error(f"unknown M2 role {role!r}")
    # m2-app already includes the base via _BASE_CHECKS; every role layers the
    # base (java client, oracle client) ahead of its specifics.
    return _BASE_CHECKS + _ROLE_CHECKS[role]


# --- read-only probe plan (runnable via a Connector) -----------------------


@dataclass(frozen=True, slots=True)
class ProbeStep:
    """One check rendered to the argv that observes it (read-only)."""

    check: Check
    argv: list[str]


def _find_argv(root: str, pattern: str, maxdepth: int) -> list[str]:
    q = shlex.quote(root)
    pat = shlex.quote(pattern)
    script = (
        f'r={q}\n'
        f'if [ -d "$r" ]; then\n'
        f'  find "$r" -maxdepth {int(maxdepth)} -type f -name {pat} 2>/dev/null | head -200\n'
        f'else\n'
        f'  echo "FIND root absent: $r"\n'
        f'fi\n'
    )
    return ["sh", "-c", script]


def _check_argv(check: Check) -> list[str]:
    if check.kind == "dir":
        return dir_state_probe(check.target)
    if check.kind == "file":
        return file_state_probe(check.target)
    if check.kind == "service":
        return service_state_probe(check.target)
    if check.kind == "package":
        return package_state_probe(check.target)
    if check.kind == "find":
        return _find_argv(check.target, check.pattern, check.maxdepth)
    if check.kind == "shell":
        return ["sh", "-c", check.target]
    raise M2Error(f"unsupported check kind {check.kind!r} (expected one of {_KINDS})")


def discovery_plan(host: str) -> list[ProbeStep]:
    """Read-only probe plan for ``host``, classified by its name.

    Each step's argv is safe to run via a Connector: it only ever stats, lists,
    or queries — it never writes, installs, or starts anything.
    """
    role = classify_host(host)
    return [ProbeStep(check=c, argv=_check_argv(c)) for c in discovery_checks(role)]


# --- standalone read-only discovery script ---------------------------------


def render_discovery_script(host: str) -> str:
    """A self-contained, read-only bash report an operator can run on ``host``.

    Equivalent to :func:`discovery_plan` but rendered as one greppable script —
    every line only observes. Safe to ``ssh host 'bash -s' < script``.
    """
    role = classify_host(host)
    lines = [
        "#!/usr/bin/env bash",
        "# Local-81 M2 stack discovery — READ-ONLY. Generated; safe to inspect.",
        f"# host={host} role={role}",
        "set -u",
        "echo " + shlex.quote(f"=== M2 discovery: {host} (role {role}) ==="),
    ]
    for check in discovery_checks(role):
        # Single-quote the header so descriptive text (which may contain '$', e.g.
        # "$JBOSS_HOME") is never expanded by the shell under `set -u`.
        lines.append("echo " + shlex.quote(f"--- {check.key}: {check.description} ---"))
        if check.kind == "find":
            q = shlex.quote(check.target)
            pat = shlex.quote(check.pattern)
            lines.append(
                f'if [ -d {q} ]; then find {q} -maxdepth {int(check.maxdepth)} -type f '
                f'-name {pat} 2>/dev/null | head -200; else echo "(root absent: {check.target})"; fi'
            )
        elif check.kind == "shell":
            lines.append(check.target)
        elif check.kind in ("dir", "file"):
            flag = "-d" if check.kind == "dir" else "-f"
            q = shlex.quote(check.target)
            lines.append(
                f'if [ {flag} {q} ]; then stat -c \'present: %n mode=%a owner=%U:%G\' {q} 2>/dev/null; '
                f'else echo "absent: {check.target}"; fi'
            )
        elif check.kind == "service":
            n = shlex.quote(check.target)
            lines.append(
                f'printf "active=%s enabled=%s\\n" "$(systemctl is-active {n} 2>/dev/null)" '
                f'"$(systemctl is-enabled {n} 2>/dev/null)"'
            )
        elif check.kind == "package":
            n = shlex.quote(check.target)
            # Single-quote the dpkg format so ${Status}/${Version} reach dpkg-query
            # verbatim instead of being expanded (and tripping `set -u`) by bash.
            lines.append(
                "if command -v dpkg-query >/dev/null 2>&1; then "
                "dpkg-query -W -f='${Status} ${Version}\\n' " + n + " 2>/dev/null || echo not-installed; "
                "elif command -v rpm >/dev/null 2>&1; then rpm -q " + n + " 2>/dev/null || echo not-installed; "
                "else echo 'no package manager'; fi"
            )
    lines.append('echo "=== end discovery ==="')
    return "\n".join(lines) + "\n"


# --- fleet catalog renderer ------------------------------------------------


def _slug(value: str) -> str:
    out = []
    for ch in value.lower():
        out.append(ch if ch.isalnum() else "-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "host"


_ROLE_HEALTH = {
    ROLE_POSTGRES: "systemctl is-active postgresql-17 2>/dev/null || true",
    ROLE_ORACLE: "test -f /etc/oratab && echo oratab-present || true",
    ROLE_MQ: "command -v dspmq >/dev/null 2>&1 && dspmq 2>/dev/null || true",
    ROLE_APP: "test -d /var/www/html/vhosts && echo vhosts-present || true",
}
_ROLE_TEST = {
    ROLE_POSTGRES: "test -d /var/lib/pgsql/17/data || test -d /var/lib/postgresql/17/main",
    ROLE_ORACLE: "test -d /opt/oracle || test -d /u01/app/oracle",
    ROLE_MQ: "test -d /var/mqm",
    ROLE_APP: "test -d /app/engin/palmed && test -d /app/smartxfr",
}


def render_m2_catalog(hosts: list[str], *, name: str = "m2-fleet", base_port: int = 2810) -> str:
    """Render a validated ``local81.recipes.v1`` catalog for a list of M2 hosts.

    Hosts are classified by name and grouped by role. The output round-trips
    through :func:`local81.recipes.load_catalog` (unique keys/aliases/ports,
    every group member a real recipe, every placeholder declared).
    """
    if not hosts:
        raise M2Error("no hosts given")

    classified: list[tuple[str, str, str]] = []  # (host, slug, role)
    seen: set[str] = set()
    for host in hosts:
        h = host.strip()
        if not h:
            continue
        slug = _slug(h)
        if slug in seen:
            raise M2Error(f"duplicate host slug {slug!r} from {h!r}")
        seen.add(slug)
        classified.append((h, slug, classify_host(h)))
    if not classified:
        raise M2Error("no usable hosts")

    # role -> [slug...] in catalog order, but only for roles actually present.
    groups: dict[str, list[str]] = {}
    for _h, slug, role in classified:
        groups.setdefault(role, []).append(slug)

    lines = [
        "# Generated by local81 m2 — an M2 stack fleet catalog.",
        "# Hosts grouped by name marker: m2in->postgres, m2or->oracle, m2tr->mq, m2->app.",
        "# Ports are unique placeholders — set each to the host's real service port.",
        "schema: local81.recipes.v1",
        f"name: {name}",
        f"description: M2 JBoss/Oracle/PostgreSQL/MQ stack fleet ({len(classified)} hosts).",
        "",
        "categories:",
        "  - key: discover",
        "    title: Discover & Observe",
        "    options:",
        '      - { key: doctor,    label: "Fleet readiness",      command: "doctor --fleet" }',
        '      - { key: pull_logs, label: "Pull stack logs",      command: "pull-logs" }',
        '      - { key: db_doctor, label: "Database readiness",   command: "db doctor" }',
        '      - { key: compliance, label: "Compliance scan",     command: "compliance report --scope {scope}" }',
        "  - key: deploy",
        "    title: Deploy & Release",
        "    options:",
        '      - { key: plan,    label: "Plan — preview",  command: "plan --summary" }',
        '      - { key: dryrun,  label: "Dry-run",         command: "deploy --latest --dry-run --forks {forks}" }',
        '      - { key: execute, label: "Deploy",          command: "deploy --latest --forks {forks}", mutating: true }',
        "",
        "parameters:",
        "  - { key: forks, type: int,  default: 5 }",
        "  - { key: scope, type: enum, default: access, choices: [access, files, all] }",
        "",
        "groups:",
    ]
    # A stable "all" group plus one group per present role.
    all_members = ", ".join(slug for _h, slug, _r in classified)
    lines.append(f'  - {{ key: all, label: "All M2 hosts", members: [{all_members}] }}')
    for role, (gkey, glabel) in ROLE_GROUP.items():
        if role in groups:
            members = ", ".join(groups[role])
            lines.append(f'  - {{ key: {gkey}, label: "{glabel}", members: [{members}] }}')

    lines += ["", "recipes:"]
    for i, (host, slug, role) in enumerate(classified):
        gkey = ROLE_GROUP[role][0]
        health = _ROLE_HEALTH[role]
        test = _ROLE_TEST[role]
        lines += [
            f"  - key: {slug}",
            f"    title: {host} ({role})",
            f"    role: {role}",
            f"    alias: {host}",
            f"    port: {base_port + i}",
            f"    group: {gkey}",
            "    build: { base: debian:12-slim, packages: [], workload: '' }",
            "    deploy: { target_dir: /app }",
            f"    health: {{ command: \"{health}\" }}",
            f"    test: {{ probe: \"{test}\" }}",
        ]
    return "\n".join(lines) + "\n"
