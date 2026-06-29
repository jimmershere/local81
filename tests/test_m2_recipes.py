from __future__ import annotations

from pathlib import Path

import pytest

from local81.recipes import (
    CatalogError,
    M2Error,
    ROLE_APP,
    ROLE_MQ,
    ROLE_ORACLE,
    ROLE_POSTGRES,
    classify_host,
    discovery_checks,
    discovery_plan,
    is_m2_host,
    load_catalog,
    render_discovery_script,
    render_m2_catalog,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "recipes" / "m2" / "fleet-m2.yaml"

# Mutating command tokens that must never appear in a read-only discovery plan.
# Chosen to be precise: e.g. the benign output string "not-installed" must not
# trip this, so we match "install " / "apt-get install" rather than bare "install".
_FORBIDDEN = (
    "rm -", "rmdir", "mkdir", "chmod ", "chown ",
    "systemctl start", "systemctl stop", "systemctl enable", "systemctl disable",
    "apt-get install", "yum install", "dnf install", "apt install",
    "createdb", "dropdb", "initdb", "pg_ctl", "crtmqm", "dltmqm",
    "truncate -", "dd if", "tee /",
)


# --- classification --------------------------------------------------------

@pytest.mark.parametrize(
    "name,role",
    [
        ("a70lspalm2in001", ROLE_POSTGRES),
        ("A70LSPALM2IN001", ROLE_POSTGRES),  # case-insensitive
        ("a70lspalm2or001", ROLE_ORACLE),
        ("a70lspalm2tr001", ROLE_MQ),
        ("a70lspalm2ex001", ROLE_APP),
        ("host-m2-web-9", ROLE_APP),
    ],
)
def test_classify_by_marker(name: str, role: str) -> None:
    assert classify_host(name) == role
    assert is_m2_host(name)


def test_specific_markers_win_over_generic_m2() -> None:
    # 'm2in' must classify as postgres even though 'm2' also matches.
    assert classify_host("xm2in9") == ROLE_POSTGRES
    assert classify_host("xm2or9") == ROLE_ORACLE
    assert classify_host("xm2tr9") == ROLE_MQ


def test_non_m2_host_fails_closed() -> None:
    with pytest.raises(M2Error, match="no 'm2' marker"):
        classify_host("a70lspalmweb001")
    assert not is_m2_host("a70lspalmweb001")


def test_empty_name_fails_closed() -> None:
    with pytest.raises(M2Error):
        classify_host("   ")


# --- discovery checks cover the right stack --------------------------------

def _keys(role: str) -> set[str]:
    return {c.key for c in discovery_checks(role)}


def test_base_checks_present_for_every_role() -> None:
    for role in (ROLE_POSTGRES, ROLE_ORACLE, ROLE_MQ, ROLE_APP):
        keys = _keys(role)
        assert {"java_client", "oracle_client", "oracle_client_net"} <= keys


def test_app_role_covers_web_jboss_engin_smartxfr_props() -> None:
    keys = _keys(ROLE_APP)
    assert {"web_vhosts", "jboss_standalone", "engin_palmed", "smartxfr",
            "app_properties", "props_dirs", "app_xml"} <= keys


def test_postgres_role_covers_pg17() -> None:
    targets = {c.target for c in discovery_checks(ROLE_POSTGRES)}
    assert "postgresql-17" in targets


def test_oracle_role_covers_oratab() -> None:
    targets = {c.target for c in discovery_checks(ROLE_ORACLE)}
    assert "/etc/oratab" in targets


def test_mq_role_covers_mqm_dirs() -> None:
    targets = {c.target for c in discovery_checks(ROLE_MQ)}
    assert "/opt/mqm" in targets and "/var/mqm" in targets


def test_unknown_role_rejected() -> None:
    with pytest.raises(M2Error):
        discovery_checks("not-a-role")


# --- the plan is genuinely read-only ---------------------------------------

def test_discovery_plan_is_read_only() -> None:
    for host in ("a70lspalm2in001", "a70lspalm2or001", "a70lspalm2tr001", "a70lspalm2ex001"):
        steps = discovery_plan(host)
        assert steps
        for step in steps:
            # argv always shells out read-only; first element is sh.
            assert step.argv[0] == "sh"
            joined = " ".join(step.argv)
            for verb in _FORBIDDEN:
                assert verb not in joined, f"{host}/{step.check.key} contains forbidden {verb!r}"


def test_discovery_script_is_read_only_and_valid_shape() -> None:
    script = render_discovery_script("a70lspalm2in001")
    assert script.startswith("#!/usr/bin/env bash")
    assert script.endswith("\n")
    assert "postgresql-17" in script
    for verb in _FORBIDDEN:
        assert verb not in script, f"discovery script contains forbidden {verb!r}"


def test_discovery_plan_non_m2_fails_closed() -> None:
    with pytest.raises(M2Error):
        discovery_plan("plainhost")


def test_discovery_script_runs_clean_under_set_u(tmp_path: Path) -> None:
    # The rendered report sets `set -u`; a dpkg format string accidentally
    # double-quoted would expand ${Status}/${Version} as unbound vars and abort.
    # Exercise every role's script through a real bash to lock that out.
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - CI always has bash
        pytest.skip("bash not available")
    for host in ("a70lspalm2in001", "a70lspalm2or001", "a70lspalm2tr001", "a70lspalm2ex001"):
        script = tmp_path / f"{host}.sh"
        script.write_text(render_discovery_script(host), encoding="utf-8")
        proc = subprocess.run([bash, str(script)], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, f"{host}: rc={proc.returncode} stderr={proc.stderr}"
        assert "unbound variable" not in proc.stderr
        assert f"M2 discovery: {host}" in proc.stdout


# --- catalog rendering round-trips through the validator -------------------

def test_rendered_catalog_round_trips() -> None:
    hosts = ["a70lspalm2ex001", "a70lspalm2in001", "a70lspalm2or001", "a70lspalm2tr001"]
    text = render_m2_catalog(hosts)
    # Write + load through the real validator.
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m2.yaml"
        p.write_text(text, encoding="utf-8")
        cat = load_catalog(p)
    assert cat.name == "m2-fleet"
    assert len(cat.recipes) == 4
    # one group per present role plus 'all'
    keys = {g.key for g in cat.groups}
    assert {"all", "m2_postgres", "m2_oracle", "m2_mq", "m2_app"} == keys
    ports = [r.port for r in cat.recipes]
    assert len(ports) == len(set(ports))


def test_rendered_catalog_groups_only_present_roles() -> None:
    text = render_m2_catalog(["a70lspalm2in001", "a70lspalm2in002"])
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m2.yaml"
        p.write_text(text, encoding="utf-8")
        cat = load_catalog(p)
    keys = {g.key for g in cat.groups}
    assert keys == {"all", "m2_postgres"}  # no empty oracle/mq/app groups


def test_render_catalog_rejects_non_m2_host() -> None:
    with pytest.raises(M2Error):
        render_m2_catalog(["a70lspalm2in001", "plainhost"])


def test_render_catalog_rejects_empty() -> None:
    with pytest.raises(M2Error):
        render_m2_catalog([])


# --- the shipped example catalog -------------------------------------------

def test_example_m2_catalog_loads() -> None:
    cat = load_catalog(EXAMPLE)
    assert cat.name == "m2-fleet"
    roles = {r.role for r in cat.recipes}
    assert roles == {ROLE_APP, ROLE_POSTGRES, ROLE_ORACLE, ROLE_MQ}
    # groups reference real recipes (validator would have raised otherwise)
    assert cat.group_aliases("m2_postgres")  # non-empty


def test_example_catalog_is_internally_consistent() -> None:
    # A malformed example would raise CatalogError on load; assert it doesn't.
    try:
        load_catalog(EXAMPLE)
    except CatalogError as exc:  # pragma: no cover - guard
        pytest.fail(f"shipped example catalog failed validation: {exc}")


# --- command layer ---------------------------------------------------------

def test_cli_classify_reports_roles_and_skips_non_m2(capsys: pytest.CaptureFixture[str]) -> None:
    from local81.commands.m2 import run_m2_classify

    rc = run_m2_classify(hosts="a70lspalm2in001,plainhost", hosts_file=None, output_format="text")
    out = capsys.readouterr()
    assert "a70lspalm2in001 -> m2-postgres" in out.out
    assert "plainhost" in out.err
    assert rc == 1  # a skipped host yields non-zero


def test_cli_plan_writes_outputs(tmp_path: Path) -> None:
    from local81.commands.m2 import run_m2_plan

    hosts_file = tmp_path / "hosts.txt"
    hosts_file.write_text("a70lspalm2in001\na70lspalm2or001\na70lspalm2tr001\na70lspalm2ex001\n", encoding="utf-8")
    out = tmp_path / "m2-out"
    rc = run_m2_plan(hosts=None, hosts_file=str(hosts_file), out=str(out), output_format="text")
    assert rc == 0
    catalog = out / "fleet-m2.yaml"
    assert catalog.is_file()
    # The generated catalog is valid.
    load_catalog(catalog)
    # One discovery script per host, each a read-only bash report.
    scripts = sorted((out / "discover").glob("*.sh"))
    assert len(scripts) == 4
    assert (out / "discovery-plan.tsv").is_file()


def test_cli_plan_json_includes_argv(capsys: pytest.CaptureFixture[str]) -> None:
    import json

    from local81.commands.m2 import run_m2_plan

    rc = run_m2_plan(hosts="a70lspalm2tr001", hosts_file=None, out=None, output_format="json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hosts"][0]["role"] == ROLE_MQ
    assert payload["hosts"][0]["checks"][0]["argv"][0] == "sh"
