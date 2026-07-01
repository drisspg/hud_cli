import subprocess
from pathlib import Path

import pytest

from hud.auth import HudConfig
from hud.gcx import (
    GcxError,
    gcx_archive_name,
    gcx_failure_message,
    gcx_status,
    gcx_subprocess_env,
    login_with_hud_token,
    validate_gcx_passthrough_args,
    validate_read_only_clickhouse_sql,
)


def test_gcx_archive_name(monkeypatch) -> None:
    monkeypatch.setattr("hud.gcx.platform.system", lambda: "Darwin")
    monkeypatch.setattr("hud.gcx.platform.machine", lambda: "arm64")

    assert gcx_archive_name("v0.4.0") == "gcx_0.4.0_darwin_arm64.tar.gz"


def test_gcx_status_finds_managed_path(monkeypatch, tmp_path: Path) -> None:
    managed = tmp_path / "gcx"
    managed.write_text("#!/bin/sh\n")
    monkeypatch.setattr("hud.gcx.shutil.which", lambda name: None)
    monkeypatch.setattr("hud.gcx.managed_gcx_path", lambda: managed)

    status = gcx_status(HudConfig())

    assert status.path == str(managed)
    assert status.source == "hud gcx install"


def test_login_with_hud_token_matches_explicit_gcx_login_shape(monkeypatch) -> None:
    captured = {}

    def fake_run_gcx(config, args):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("hud.gcx.mint_gcx_token", lambda config, token_name: "grafana-token")
    monkeypatch.setattr("hud.gcx.run_gcx", fake_run_gcx)

    login_with_hud_token(HudConfig(github_token="github-token"), "host-token")

    assert captured["args"] == [
        "login",
        "pytorchci",
        "--server",
        "https://pytorchci.grafana.net",
        "--yes",
        "--token",
        "grafana-token",
    ]


def test_gcx_failure_message_explains_unauthorized() -> None:
    result = subprocess.CompletedProcess(["gcx"], 1, "", 'Error: HTTP 401\n{"message":"Unauthorized"}')

    message = gcx_failure_message(result, "fallback")

    assert "hud gcx login" in message
    assert "gh auth status" in message
    assert "--token ..." in message


def test_gcx_subprocess_env_removes_server_without_token(monkeypatch) -> None:
    monkeypatch.setenv("GRAFANA_SERVER", "https://example.invalid")
    monkeypatch.delenv("GRAFANA_TOKEN", raising=False)

    env = gcx_subprocess_env()

    assert "GRAFANA_SERVER" not in env


def test_gcx_subprocess_env_sets_server_for_token_mode(monkeypatch) -> None:
    monkeypatch.delenv("GRAFANA_SERVER", raising=False)
    monkeypatch.setenv("GRAFANA_TOKEN", "grafana-token")

    env = gcx_subprocess_env()

    assert env["GRAFANA_SERVER"] == "https://pytorchci.grafana.net"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT conclusion, count() FROM default.workflow_job GROUP BY conclusion",
        "WITH recent AS (SELECT * FROM default.workflow_job) SELECT count() FROM recent",
        "SHOW TABLES FROM default",
        "DESCRIBE TABLE default.workflow_job",
        "EXPLAIN SELECT count() FROM default.workflow_job",
        "SELECT table, name FROM system.columns WHERE database = 'default'",
        "SELECT 'DROP TABLE is just a string';",
        "-- DROP TABLE in a comment\nSELECT 1",
    ],
)
def test_validate_read_only_clickhouse_sql_allows_read_queries(sql: str) -> None:
    validate_read_only_clickhouse_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO default.workflow_job VALUES (1)",
        "ALTER TABLE default.workflow_job DELETE WHERE 1",
        "DROP TABLE default.workflow_job",
        "TRUNCATE TABLE default.workflow_job",
        "OPTIMIZE TABLE default.workflow_job",
        "SYSTEM FLUSH LOGS",
        "SET max_threads = 1",
        "SELECT 1; DROP TABLE default.workflow_job",
        "WITH x AS (SELECT 1) INSERT INTO default.workflow_job SELECT * FROM x",
    ],
)
def test_validate_read_only_clickhouse_sql_blocks_write_and_admin_queries(sql: str) -> None:
    with pytest.raises(GcxError):
        validate_read_only_clickhouse_sql(sql)


def test_validate_gcx_passthrough_args_blocks_clickhouse_writes() -> None:
    with pytest.raises(GcxError):
        validate_gcx_passthrough_args(
            [
                "api",
                "/api/ds/query",
                "-d",
                '{"queries":[{"datasource":{"type":"grafana-clickhouse-datasource"},"rawSql":"DROP TABLE default.workflow_job"}]}',
            ]
        )


def test_validate_gcx_passthrough_args_allows_other_gcx_commands() -> None:
    validate_gcx_passthrough_args(["datasources", "list"])
