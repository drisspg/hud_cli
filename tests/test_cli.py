import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import hud.cli as cli
from hud.cli import app

runner = CliRunner()


def fake_clickhouse_response():
    return {
        "results": {
            "A": {
                "frames": [
                    {
                        "schema": {"fields": [{"name": "conclusion"}, {"name": "n"}]},
                        "data": {"values": [["success", "failure"], [12, 3]]},
                    }
                ]
            }
        }
    }


def fake_clickhouse_query(config, sql):
    return fake_clickhouse_response()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "PyTorch CI" in result.output
    assert "gcx" in result.output
    assert "log" in result.output
    assert "status" not in result.output
    assert "recipe" not in result.output


@pytest.mark.parametrize("args", [["doctor"], ["auth", "doctor"]])
def test_auth_doctor(args) -> None:
    result = runner.invoke(app, args)

    assert result.exit_code == 0
    assert "hud auth" in result.output
    assert "GITHUB_TOKEN/gh auth" in result.output
    assert "GRAFANA_TOKEN" in result.output
    assert "Grafana gcx" in result.output


def test_auth_setup() -> None:
    result = runner.invoke(app, ["auth", "setup"])

    assert result.exit_code == 0
    assert "gh auth login" in result.output
    assert "hud gcx login" in result.output
    assert "hud gcx chq" in result.output


def test_removed_hud_rate_limited_commands() -> None:
    for args in [["status"], ["query"], ["recipe"], ["search"]]:
        result = runner.invoke(app, args)
        assert result.exit_code != 0
        assert "No such command" in result.output


def test_gcx_doctor_json_reports_missing() -> None:
    result = runner.invoke(
        app,
        ["gcx", "doctor", "--json"],
        env={"PATH": "", "GRAFANA_TOKEN": "read-only-token", "HUD_GCX_PATH": "/tmp/missing-gcx"},
    )

    assert result.exit_code == 0
    assert '"available": false' in result.output
    assert '"grafana_token_set": true' in result.output


def test_gcx_install(monkeypatch, tmp_path) -> None:
    managed = tmp_path / "gcx"
    monkeypatch.setattr(cli, "install_gcx", lambda force=False: managed)

    result = runner.invoke(app, ["gcx", "install"])

    assert result.exit_code == 0
    assert "gcx" in result.output


def test_gcx_login_uses_hud_minted_token(monkeypatch) -> None:
    captured = {}

    def fake_login(config, token_name):
        captured["token_name"] = token_name
        return subprocess.CompletedProcess(["gcx"], 0, "logged in\n", "")

    monkeypatch.setattr(cli, "login_with_hud_token", fake_login)
    monkeypatch.setattr(cli, "hostname_token_name", lambda: "test-host")

    result = runner.invoke(app, ["gcx", "login"])

    assert result.exit_code == 0
    assert captured["token_name"] == "test-host"
    assert "logged in" in result.output


def test_gcx_chq_outputs_rows(monkeypatch) -> None:
    monkeypatch.setattr(cli, "clickhouse_query", fake_clickhouse_query)

    result = runner.invoke(app, ["gcx", "chq", "SELECT conclusion, count() AS n FROM default.workflow_job", "--json"])

    assert result.exit_code == 0
    assert '"conclusion": "success"' in result.output
    assert '"n": 3' in result.output


def test_gcx_chq_reads_sql_file(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_query(config, sql):
        captured["sql"] = sql
        return fake_clickhouse_response()

    sql_file = tmp_path / "query.sql"
    sql_file.write_text("SELECT 1\n")
    monkeypatch.setattr(cli, "clickhouse_query", fake_query)

    result = runner.invoke(app, ["gcx", "chq", "--file", str(sql_file), "--json"])

    assert result.exit_code == 0
    assert captured["sql"] == "SELECT 1\n"


def test_gcx_chq_reads_stdin(monkeypatch) -> None:
    captured = {}

    def fake_query(config, sql):
        captured["sql"] = sql
        return fake_clickhouse_response()

    monkeypatch.setattr(cli, "clickhouse_query", fake_query)

    result = runner.invoke(app, ["gcx", "chq", "-", "--json"], input="SELECT 2\n")

    assert result.exit_code == 0
    assert captured["sql"] == "SELECT 2\n"


def test_gcx_run_reports_missing() -> None:
    result = runner.invoke(app, ["gcx", "run", "--", "--help"], env={"PATH": "", "HUD_GCX_PATH": "/tmp/missing-gcx"})

    assert result.exit_code == 1
    assert "gcx is not available" in result.output


def test_gcx_run_passthrough(tmp_path: Path) -> None:
    gcx = tmp_path / "gcx"
    gcx.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 7\n')
    gcx.chmod(0o755)

    result = runner.invoke(
        app,
        ["gcx", "run", "--", "datasources", "list"],
        env={"HUD_GCX_PATH": str(gcx)},
    )

    assert result.exit_code == 7
    assert "datasources" in result.output
    assert "list" in result.output


def test_job_summary_includes_direct_log_url() -> None:
    result = runner.invoke(app, ["job", "123", "--json"])

    assert result.exit_code == 0
    assert '"job_id": 123' in result.output
    assert "https://ossci-raw-job-status.s3.amazonaws.com/log/123" in result.output


def test_log_url() -> None:
    result = runner.invoke(app, ["log", "url", "123"])

    assert result.exit_code == 0
    assert "https://ossci-raw-job-status.s3.amazonaws.com/log/123" in result.output


def test_log_search_local_file(tmp_path: Path) -> None:
    log = tmp_path / "job.log"
    log.write_text("ok\nRuntimeError: boom\nok\n")

    result = runner.invoke(app, ["log", "search", "runtimeerror", "--path", str(log), "--json"])

    assert result.exit_code == 0
    assert '"line_number": 2' in result.output
    assert "RuntimeError: boom" in result.output


def test_log_patterns(tmp_path: Path) -> None:
    log = tmp_path / "job.log"
    log.write_text("warning: heads up\nCUDA error: boom\n")

    result = runner.invoke(app, ["log", "patterns", str(log), "--json"])

    assert result.exit_code == 0
    assert '"warning": 1' in result.output
    assert '"cuda_error": 1' in result.output


def test_log_tests(tmp_path: Path) -> None:
    log = tmp_path / "job.log"
    log.write_text(
        "FAIL: test_foo\n"
        "FAILED test/test_cuda.py::TestCuda::test_bar - RuntimeError: boom\n"
        "=== 2 failed, 2 passed, 3 skipped in 4.5s ===\n"
    )

    result = runner.invoke(app, ["log", "tests", str(log), "--json"])

    assert result.exit_code == 0
    assert '"duration_seconds": 4.5' in result.output
    assert '"failed": 2' in result.output
    assert '"passed": 2' in result.output
    assert '"skipped": 3' in result.output
    assert '"test_name": "test_foo"' in result.output
    assert '"test_name": "test/test_cuda.py::TestCuda::test_bar"' in result.output


def test_log_sections(tmp_path: Path) -> None:
    log = tmp_path / "job.log"
    log.write_text("pre\nSTART\ninteresting\nEND\npost\n")

    result = runner.invoke(
        app,
        ["log", "sections", str(log), "--start", "START", "--end", "END", "--json"],
    )

    assert result.exit_code == 0
    assert '"section_count": 1' in result.output
    assert "interesting" in result.output
