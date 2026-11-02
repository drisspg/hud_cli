import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from hud.auth import HudConfig
from hud.gcx import (
    GcxError,
    ensure_gcx,
    gcx_archive_name,
    gcx_failure_message,
    gcx_status,
    gcx_subprocess_env,
    install_gcx,
    login_with_hud_token,
    validate_gcx_passthrough_args,
    validate_read_only_clickhouse_sql,
    verify_sha256,
)


def test_gcx_archive_name(monkeypatch) -> None:
    monkeypatch.setattr("hud.gcx.platform.system", lambda: "Darwin")
    monkeypatch.setattr("hud.gcx.platform.machine", lambda: "arm64")

    assert gcx_archive_name("v0.4.0") == "gcx_0.4.0_darwin_arm64.tar.gz"


def test_gcx_status_finds_managed_path(monkeypatch, tmp_path: Path) -> None:
    managed = tmp_path / "gcx"
    managed.write_text("#!/bin/sh\n")
    monkeypatch.setattr("hud.gcx.managed_gcx_path", lambda: managed)

    status = gcx_status(HudConfig())

    assert status.path == str(managed)
    assert status.source == "managed v1.7.1"


def test_gcx_status_ignores_gcx_on_path(monkeypatch, tmp_path: Path) -> None:
    on_path = tmp_path / "bin"
    on_path.mkdir()
    (on_path / "gcx").write_text("#!/bin/sh\n")
    (on_path / "gcx").chmod(0o755)
    monkeypatch.setenv("PATH", str(on_path))
    monkeypatch.setattr("hud.gcx.managed_gcx_path", lambda: tmp_path / "missing")

    assert gcx_status(HudConfig()).path is None


def test_ensure_gcx_installs_managed_binary_when_missing(monkeypatch, tmp_path: Path) -> None:
    installed = tmp_path / "gcx-v1.7.1"
    monkeypatch.setattr("hud.gcx.managed_gcx_path", lambda: tmp_path / "missing")
    monkeypatch.setattr("hud.gcx.install_gcx", lambda: installed)

    assert ensure_gcx(HudConfig()) == str(installed)


def test_ensure_gcx_rejects_missing_override(tmp_path: Path) -> None:
    with pytest.raises(GcxError, match="GCX_PATH"):
        ensure_gcx(HudConfig(gcx_path=str(tmp_path / "missing")))


def test_install_gcx_verifies_and_publishes_binary(monkeypatch, tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo("gcx")
        info.size = len(b"binary")
        tar.addfile(info, io.BytesIO(b"binary"))
    archive = buffer.getvalue()
    archive_name = "gcx_1.7.1_darwin_arm64.tar.gz"
    assets = {
        archive_name: archive,
        "gcx_1.7.1_checksums.txt": f"{hashlib.sha256(archive).hexdigest()}  {archive_name}\n".encode(),
    }
    monkeypatch.setattr("hud.gcx.GCX_INSTALL_DIR", tmp_path)
    monkeypatch.setattr("hud.gcx.managed_gcx_path", lambda version: tmp_path / f"gcx-{version}")
    monkeypatch.setattr("hud.gcx.gcx_archive_name", lambda version: archive_name)
    monkeypatch.setattr("hud.gcx.download_release_asset", lambda url: assets[url.rsplit("/", 1)[1]])

    installed = install_gcx("v1.7.1")

    assert installed.read_bytes() == b"binary"
    assert installed.stat().st_mode & 0o111
    assert sorted(path.name for path in tmp_path.iterdir()) == ["gcx-v1.7.1"]


def test_verify_sha256_accepts_matching_digest() -> None:
    digest = hashlib.sha256(b"archive").hexdigest()

    verify_sha256(b"archive", "gcx.tar.gz", f"deadbeef  other.tar.gz\n{digest}  gcx.tar.gz\n")


@pytest.mark.parametrize(
    "checksums",
    ["0000  gcx.tar.gz\n", f"{hashlib.sha256(b'archive').hexdigest()}  other.tar.gz\n"],
)
def test_verify_sha256_rejects_mismatch_or_missing_entry(checksums: str) -> None:
    with pytest.raises(GcxError):
        verify_sha256(b"archive", "gcx.tar.gz", checksums)


def test_login_with_hud_token_passes_token_through_env_not_args(monkeypatch) -> None:
    captured = {}

    def fake_run_gcx(config, args, extra_env=None):
        captured["args"] = args
        captured["env"] = extra_env
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
    ]
    assert captured["env"] == {"GRAFANA_TOKEN": "grafana-token"}


def test_gcx_failure_message_explains_unauthorized() -> None:
    result = subprocess.CompletedProcess(["gcx"], 1, "", 'Error: HTTP 401\n{"message":"Unauthorized"}')

    message = gcx_failure_message(result, "fallback")

    assert "hud gcx login" in message
    assert "gh auth status" in message
    assert "GRAFANA_TOKEN" in message


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


def test_gcx_subprocess_env_isolates_hud_gcx_config(monkeypatch) -> None:
    monkeypatch.setenv("GCX_CONFIG", "/home/user/.config/gcx/config.yaml")
    monkeypatch.delenv("GCX_KEYCHAIN", raising=False)

    env = gcx_subprocess_env()

    assert env["GCX_CONFIG"].endswith("gcx.yaml")
    assert "hud-cli" in env["GCX_CONFIG"]
    assert env["GCX_KEYCHAIN"] == "off"


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


def test_validate_gcx_passthrough_args_routes_native_clickhouse_query_to_chq() -> None:
    with pytest.raises(GcxError, match="hud gcx chq"):
        validate_gcx_passthrough_args(["datasources", "clickhouse", "query", "-d", "uid", "DROP TABLE x"])


def test_validate_gcx_passthrough_args_allows_other_gcx_commands() -> None:
    validate_gcx_passthrough_args(["datasources", "list"])
