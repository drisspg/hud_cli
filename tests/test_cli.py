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


def fake_clickhouse_query(config, sql, settings=None):
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
        env={"PATH": "", "GRAFANA_TOKEN": "read-only-token", "GCX_PATH": "/tmp/missing-gcx"},
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
    monkeypatch.setattr(cli, "clickhouse_query", fake_clickhouse_query)
    monkeypatch.setattr(cli, "hostname_token_name", lambda: "test-host")

    result = runner.invoke(app, ["gcx", "login"])

    assert result.exit_code == 0
    assert captured["token_name"] == "test-host"
    assert "Logged gcx context pytorchci" in result.output
    assert "Verified HUD ClickHouse access" in result.output


def test_gcx_login_reports_verification_failure(monkeypatch) -> None:
    def fake_login(config, token_name):
        return subprocess.CompletedProcess(["gcx"], 0, "logged in\n", "")

    def fake_query(config, sql, settings=None):
        raise cli.GcxError("Grafana returned 401 Unauthorized for the gcx pytorchci context. Run `hud gcx login`.")

    monkeypatch.setattr(cli, "login_with_hud_token", fake_login)
    monkeypatch.setattr(cli, "clickhouse_query", fake_query)

    result = runner.invoke(app, ["gcx", "login"])

    assert result.exit_code == 1
    assert "gcx login completed, but HUD ClickHouse verification failed" in result.output
    assert "401 Unauthorized" in result.output


def test_gcx_chq_outputs_rows(monkeypatch) -> None:
    monkeypatch.setattr(cli, "clickhouse_query", fake_clickhouse_query)

    result = runner.invoke(app, ["gcx", "chq", "SELECT conclusion, count() AS n FROM default.workflow_job", "--json"])

    assert result.exit_code == 0
    assert '"conclusion": "success"' in result.output
    assert '"n": 3' in result.output


def test_gcx_chq_reads_sql_file(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_query(config, sql, settings=None):
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

    def fake_query(config, sql, settings=None):
        captured["sql"] = sql
        return fake_clickhouse_response()

    monkeypatch.setattr(cli, "clickhouse_query", fake_query)

    result = runner.invoke(app, ["gcx", "chq", "-", "--json"], input="SELECT 2\n")

    assert result.exit_code == 0
    assert captured["sql"] == "SELECT 2\n"


@pytest.mark.parametrize(
    ("args", "expected_sql"),
    [
        (["gcx", "describe", "workflow_job", "--json"], "DESCRIBE TABLE `default`.`workflow_job`"),
        (["gcx", "sample", "workflow_job", "--limit", "3", "--json"], "SELECT * FROM `default`.`workflow_job` LIMIT 3"),
    ],
)
def test_gcx_discovery_commands(monkeypatch, args, expected_sql) -> None:
    captured = {}

    def fake_query(config, sql, settings=None):
        captured["sql"] = sql
        return fake_clickhouse_response()

    monkeypatch.setattr(cli, "clickhouse_query", fake_query)

    result = runner.invoke(app, args)

    assert result.exit_code == 0
    assert captured["sql"] == expected_sql


@pytest.mark.parametrize(
    ("args", "expected_filter"),
    [
        (["gcx", "tables", "--json"], "database NOT IN ('system'"),
        (["gcx", "tables", "-d", "ci", "--json"], "database = 'ci'"),
        (["gcx", "columns", "owner", "--json"], "database NOT IN ('system'"),
    ],
)
def test_gcx_discovery_spans_databases_by_default(monkeypatch, args, expected_filter) -> None:
    captured = {}

    def fake_query(config, sql, settings=None):
        captured["sql"] = sql
        return fake_clickhouse_response()

    monkeypatch.setattr(cli, "clickhouse_query", fake_query)

    result = runner.invoke(app, args)

    assert result.exit_code == 0
    assert expected_filter in captured["sql"]


def test_gcx_columns_search(monkeypatch) -> None:
    captured = {}

    def fake_query(config, sql, settings=None):
        captured["sql"] = sql
        return fake_clickhouse_response()

    monkeypatch.setattr(cli, "clickhouse_query", fake_query)

    result = runner.invoke(app, ["gcx", "columns", "test", "-d", "default", "--table", "workflow_job", "--limit", "7", "--json"])

    assert result.exit_code == 0
    assert "FROM system.columns" in captured["sql"]
    assert "database = 'default'" in captured["sql"]
    assert "table = 'workflow_job'" in captured["sql"]
    assert "ILIKE '%test%'" in captured["sql"]
    assert "LIMIT 7" in captured["sql"]


def test_gcx_run_reports_missing() -> None:
    result = runner.invoke(app, ["gcx", "run", "--", "--help"], env={"PATH": "", "GCX_PATH": "/tmp/missing-gcx"})

    assert result.exit_code == 1
    assert "gcx is not available" in result.output


def test_gcx_run_passthrough(tmp_path: Path) -> None:
    gcx = tmp_path / "gcx"
    gcx.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 7\n')
    gcx.chmod(0o755)

    result = runner.invoke(
        app,
        ["gcx", "run", "--", "datasources", "list"],
        env={"GCX_PATH": str(gcx)},
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


def test_log_download_defaults_to_cache_dir(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_download(job_id, destination):
        captured["destination"] = destination
        return destination

    monkeypatch.setattr(cli, "LOG_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cli, "download_log", fake_download)

    result = runner.invoke(app, ["log", "download", "123"])

    assert result.exit_code == 0
    assert captured["destination"] == tmp_path / "hud-job-123.log"


def test_tests_api_passes_path_and_repeated_query_params(monkeypatch) -> None:
    captured = {}

    def fake_get(config, path, params):
        captured["path"] = path
        captured["params"] = params
        return {"tests": [], "hud_url": "https://hud.pytorch.org/tests/pytorch/pytorch"}

    monkeypatch.setattr(cli, "tests_api_get", fake_get)

    result = runner.invoke(
        app,
        ["tests", "api", "/api/tests", "-q", "repo=pytorch/pytorch", "-q", "owner=nn", "-q", "owner=optim"],
    )

    assert result.exit_code == 0
    assert captured["path"] == "/api/tests"
    assert captured["params"] == [("repo", "pytorch/pytorch"), ("owner", "nn"), ("owner", "optim")]
    assert "hud_url" in result.output


def test_tests_api_rejects_malformed_query_param() -> None:
    result = runner.invoke(app, ["tests", "api", "/api/tests", "-q", "repo"])

    assert result.exit_code == 1
    assert "key=value" in result.output


def test_tests_schema_lists_parameters(monkeypatch) -> None:
    schema = {
        "paths": {
            "/api/tests": {
                "get": {
                    "summary": "List tests",
                    "tags": ["tests"],
                    "parameters": [
                        {"name": "repo", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "owner", "in": "query", "schema": {"type": "array", "items": {"type": "string"}}},
                    ],
                }
            }
        }
    }
    monkeypatch.setattr(cli, "tests_api_get", lambda config, path, params=None: schema)

    endpoints = runner.invoke(app, ["tests", "schema", "--json"])
    parameters = runner.invoke(app, ["tests", "schema", "/api/tests", "--json"])
    unknown = runner.invoke(app, ["tests", "schema", "/api/nope"])

    assert '"kind": "api"' in endpoints.output
    assert '"type": "string[]"' in parameters.output
    assert unknown.exit_code == 1
    assert "unknown Tests API path" in unknown.output


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


def test_gcx_chq_renders_params_and_sets_timeout(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_query(config, sql, settings=None):
        captured["sql"] = sql
        captured["settings"] = settings
        return fake_clickhouse_response()

    sql_file = tmp_path / "query.sql"
    sql_file.write_text("-- @param days=1\nSELECT {owner:String}, {days:UInt32}\n")
    monkeypatch.setattr(cli, "clickhouse_query", fake_query)

    result = runner.invoke(app, ["gcx", "chq", "-f", str(sql_file), "-p", "owner=nn", "--max-seconds", "30", "--json"])

    assert result.exit_code == 0
    assert "SELECT 'nn', 1" in captured["sql"]
    assert captured["settings"] == {"max_execution_time": 30}


def test_gcx_chq_reports_missing_param(monkeypatch) -> None:
    monkeypatch.setattr(cli, "clickhouse_query", fake_clickhouse_query)

    result = runner.invoke(app, ["gcx", "chq", "SELECT {owner:String}"])

    assert result.exit_code == 1
    assert "missing SQL parameters: owner" in result.output


def test_gcx_chq_csv_and_output_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "clickhouse_query", fake_clickhouse_query)
    out = tmp_path / "rows.csv"

    printed = runner.invoke(app, ["gcx", "chq", "SELECT 1", "--csv"])
    written = runner.invoke(app, ["gcx", "chq", "SELECT 1", "--csv", "-o", str(out)])

    assert printed.output.splitlines() == ["conclusion,n", "success,12", "failure,3"]
    assert written.exit_code == 0
    assert out.read_text().splitlines()[0] == "conclusion,n"


def test_tests_api_all_reports_truncation_at_page_cap(monkeypatch) -> None:
    monkeypatch.setattr(
        "hud.tests_api.tests_api_get",
        lambda config, path, params: {"tests": [1], "pagination": {"next": {"cursor": "again"}}},
    )

    result = runner.invoke(app, ["tests", "api", "/api/tests", "--all", "--max-pages", "2"])

    assert '"pages_fetched": 2' in result.output
    assert '"truncated": true' in result.output


def test_tests_api_all_follows_cursors(monkeypatch) -> None:
    pages = {
        None: {"tests": [1, 2], "pagination": {"next": {"cursor": "c2"}}},
        "c2": {"tests": [3], "pagination": {"next": None}},
    }
    calls = []

    def fake_get(config, path, params):
        cursor = dict(params).get("cursor")
        calls.append(params)
        return {key: list(value) if isinstance(value, list) else value for key, value in pages[cursor].items()}

    monkeypatch.setattr("hud.tests_api.tests_api_get", fake_get)

    result = runner.invoke(app, ["tests", "api", "/api/tests", "-q", "repo=pytorch/pytorch", "--all"])

    assert result.exit_code == 0
    assert '"pages_fetched": 2' in result.output
    assert '"truncated": false' in result.output
    assert calls[1] == [("repo", "pytorch/pytorch"), ("cursor", "c2")]
    assert result.output.count("\n    3") == 1
