from pathlib import Path

import pytest
from typer.testing import CliRunner

import hud.cli as cli
from hud.cli import app, parse_params, parse_repo

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "PyTorch HUD" in result.output


@pytest.mark.parametrize("args", [["doctor"], ["auth", "doctor"]])
def test_auth_doctor(args) -> None:
    result = runner.invoke(app, args)

    assert result.exit_code == 0
    assert "HUD auth" in result.output
    assert "HUD_API_TOKEN" in result.output
    assert "GRAFANA_TOKEN" in result.output
    assert "gcx" in result.output


def test_gcx_doctor_json_reports_missing() -> None:
    result = runner.invoke(
        app,
        ["gcx", "doctor", "--json"],
        env={"PATH": "", "GRAFANA_TOKEN": "read-only-token"},
    )

    assert result.exit_code == 0
    assert '"available": false' in result.output
    assert '"grafana_token_set": true' in result.output


def test_gcx_run_reports_missing() -> None:
    result = runner.invoke(app, ["gcx", "run", "--", "--help"], env={"PATH": ""})

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


def test_parse_repo() -> None:
    assert parse_repo("pytorch/pytorch") == ("pytorch", "pytorch")


def test_parse_params() -> None:
    assert parse_params(["limit=10", "enabled=true", "ratio=1.5", "name=linux"]) == {
        "limit": 10,
        "enabled": True,
        "ratio": 1.5,
        "name": "linux",
    }


def test_query_list() -> None:
    result = runner.invoke(app, ["query", "list"])

    assert result.exit_code == 0
    assert "queued_jobs" in result.output
    assert "master_commit_red" in result.output


def test_query_explain_json() -> None:
    result = runner.invoke(app, ["query", "explain", "queued_jobs", "--json"])

    assert result.exit_code == 0
    assert '"name": "queued_jobs"' in result.output


@pytest.mark.parametrize(
    ("args", "expected_query", "expected_params"),
    [
        (["queued"], "queued_jobs", {}),
        (["trunk-red", "--days", "3"], "master_commit_red", {"granularity": "day", "usePercentage": True}),
        (["flaky-test", "test_foo", "--days", "1"], "flaky_tests/across_jobs", {"test_name": "test_foo"}),
        (["tts", "--percentile", "--days", "1"], "tts_percentile", {}),
        (["disabled-tests", "--days", "2"], "disabled_test_historical", {"label": "skipped"}),
        (["slow-test-files", "--limit", "2"], "test_time_per_file", {}),
    ],
)
def test_recipe_batch_uses_named_queries(monkeypatch, args, expected_query, expected_params) -> None:
    fake_client = FakeClient()
    monkeypatch.setattr(cli, "new_client", lambda: fake_client)

    result = runner.invoke(app, ["recipe", *args])

    assert result.exit_code == 0
    query_name, params = fake_client.calls[0]
    assert query_name == expected_query
    assert "startTime" in params or expected_query in {"queued_jobs", "test_time_per_file"}
    assert "stopTime" in params or expected_query in {"queued_jobs", "test_time_per_file"}
    for key, value in expected_params.items():
        assert params[key] == value
    if expected_query == "test_time_per_file":
        assert result.output.index('"file": "test_slow"') < result.output.index('"file": "test_medium"')
        assert "test_fast" not in result.output
    else:
        assert '"ok": true' in result.output


def test_search_failures_uses_bounded_search(monkeypatch) -> None:
    fake_client = FakeClient()
    monkeypatch.setattr(cli, "new_client", lambda: fake_client)

    result = runner.invoke(
        app,
        ["search", "failures", "CUDA out of memory", "--workflow-name", "linux", "--days", "3"],
    )

    assert result.exit_code == 0
    assert fake_client.search_calls[0]["failure"] == "CUDA out of memory"
    assert fake_client.search_calls[0]["workflow_name"] == "linux"
    assert fake_client.search_calls[0]["days"] == 3
    assert '"matches": []' in result.output


def test_status_compact_json_filters_failures(monkeypatch) -> None:
    fake_client = FakeClient()
    monkeypatch.setattr(cli, "new_client", lambda: fake_client)

    result = runner.invoke(app, ["status", "main", "--failures", "--compact-json"])

    assert result.exit_code == 0
    assert '"short_sha": "abcdef1234"' in result.output
    assert '"name": "linux-test"' in result.output
    assert '"name": "macos-test"' not in result.output


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


def test_query_source(monkeypatch) -> None:
    monkeypatch.setattr(cli, "fetch_query_source", lambda name, filename: f"{name}:{filename}")

    result = runner.invoke(app, ["query", "source", "queued_jobs", "--json"])

    assert result.exit_code == 0
    assert '"query_sql": "queued_jobs:query.sql"' in result.output
    assert '"params_json": "queued_jobs:params.json"' in result.output


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.search_calls: list[dict] = []

    def clickhouse_query(self, name: str, parameters: dict):
        self.calls.append((name, parameters))
        if name == "test_time_per_file":
            return [
                {"file": "test_fast", "base_name": "linux", "test_config": "default", "time": 1.0},
                {"file": "test_slow", "base_name": "linux", "test_config": "default", "time": 10.0},
                {"file": "test_medium", "base_name": "linux", "test_config": "default", "time": 5.0},
            ]
        return {"ok": True, "query": name, "parameters": parameters}

    def similar_failures(
        self,
        failure: str,
        repo: str | None,
        workflow_name: str | None,
        branch_name: str | None,
        start_date: str | None,
        end_date: str | None,
        min_score: float,
        days: int,
    ) -> dict:
        self.search_calls.append(
            {
                "failure": failure,
                "repo": repo,
                "workflow_name": workflow_name,
                "branch_name": branch_name,
                "start_date": start_date,
                "end_date": end_date,
                "min_score": min_score,
                "days": days,
            }
        )
        return {"matches": [], "total_matches": 0}

    def hud_data(
        self, repo_owner: str, repo_name: str, branch_or_sha: str, page: int, per_page: int
    ) -> dict:
        return {
            "jobNames": ["linux-test", "macos-test"],
            "shaGrid": [
                {
                    "sha": "abcdef1234567890",
                    "commitTitle": "test commit",
                    "author": "dev",
                    "jobs": [
                        {"id": 0, "conclusion": "failure", "failureLine": "RuntimeError"},
                        {"id": 1, "conclusion": "success"},
                    ],
                }
            ],
        }
