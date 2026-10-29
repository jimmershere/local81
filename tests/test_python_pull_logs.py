from __future__ import annotations

from pathlib import Path

from local81.commands.pull_logs import _load_settings, run_pull_logs


def _write_settings(tmp_path: Path) -> Path:
    settings_path = tmp_path / "settings.cfg"
    settings_path.write_text(
        "\n".join([
            "log_hosts = app1,app2",
            "log_dest_dir = ./collected",
            "jboss_log_path = /var/log/jboss/server.log",
            "apache_log_path = /var/log/httpd/access.log",
            "",
        ]),
        encoding="utf-8",
    )
    return settings_path


def test_load_settings(tmp_path: Path) -> None:
    settings_path = _write_settings(tmp_path)
    data = _load_settings(settings_path)
    assert data["log_hosts"] == "app1,app2"
    assert data["apache_log_path"] == "/var/log/httpd/access.log"


def test_run_pull_logs_reads_settings_and_invokes_scp(tmp_path: Path, monkeypatch, capsys) -> None:
    settings_path = _write_settings(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    class Result:
        returncode = 0

    def fake_run(command: list[str], *, stdout, stderr, text: bool):
        calls.append(command)
        target_dir = Path(command[-1].rstrip("/"))
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "pulled.log").write_text("ok", encoding="utf-8")
        assert command[:3] == ["scp", "-q", "-r"]
        assert text is True
        return Result()

    monkeypatch.setattr("local81.commands.pull_logs.subprocess.run", fake_run)

    rc = run_pull_logs(settings=str(settings_path))

    out = capsys.readouterr().out
    assert rc == 0
    assert len(calls) == 4
    assert calls[0] == ["scp", "-q", "-r", "app1:/var/log/jboss/server.log", "collected/app1/jboss/"]
    assert calls[-1] == ["scp", "-q", "-r", "app2:/var/log/httpd/access.log", "collected/app2/apache/"]
    assert "Pulled logs to ./collected (success=4, failed=0)" in out


def test_run_pull_logs_honors_cli_overrides(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    calls: list[list[str]] = []

    class Result:
        returncode = 0

    def fake_run(command: list[str], *, stdout, stderr, text: bool):
        calls.append(command)
        return Result()

    monkeypatch.setattr("local81.commands.pull_logs.subprocess.run", fake_run)

    rc = run_pull_logs(hosts="h1", dest=str(tmp_path / "logs"), jboss_path="/tmp/jboss.log")

    out = capsys.readouterr().out
    assert rc == 0
    assert calls == [["scp", "-q", "-r", "h1:/tmp/jboss.log", f"{tmp_path / 'logs' / 'h1' / 'jboss'}/"]]
    assert f"Pulled logs to {tmp_path / 'logs'} (success=1, failed=0)" in out


def test_run_pull_logs_requires_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    rc = run_pull_logs(hosts="h1")

    captured = capsys.readouterr()
    assert rc == 1
    assert "no log paths configured" in captured.err


def test_cli_pull_logs_command() -> None:
    from local81.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "pull-logs",
        "--settings", "./settings.cfg",
        "--hosts", "h1,h2",
        "--dest", "./logs",
        "--jboss-path", "/tmp/jboss.log",
        "--apache-path", "/tmp/apache.log",
    ])
    assert args.command == "pull-logs"
    assert args.settings == "./settings.cfg"
    assert args.hosts == "h1,h2"
    assert args.dest == "./logs"
    assert args.jboss_path == "/tmp/jboss.log"
    assert args.apache_path == "/tmp/apache.log"
