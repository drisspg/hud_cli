from pathlib import Path

import pytest

from hud.auth import HudConfig
from hud.gcx import (
    GcxError,
    gcx_archive_name,
    gcx_status,
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
