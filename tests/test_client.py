import subprocess

import pytest

from hud.auth import HudConfig, load_config
from hud.client import HudClient, HudError, RetryPolicy


class FakeResponse:
    def __init__(self, status_code: int, payload=None) -> None:
        self.status_code = status_code
        self.payload = payload if payload is not None else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self) -> dict:
        return self.payload


def test_headers_include_optional_tokens() -> None:
    client = HudClient(
        HudConfig(api_token="hud-token", github_token="github-token")
    )

    assert client._headers()["x-hud-internal-bot"] == "hud-token"
    assert client._headers()["authorization"] == "Bearer github-token"


def test_load_config_accepts_only_hud_api_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HUD_API_TOKEN", "hud-api-token")
    monkeypatch.setenv("HUD_INTERNAL_BOT_TOKEN", "legacy-token")

    assert load_config(tmp_path / "missing.toml").api_token == "hud-api-token"


def test_load_config_ignores_legacy_hud_internal_bot_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HUD_INTERNAL_BOT_TOKEN", "legacy-token")

    assert load_config(tmp_path / "missing.toml").api_token is None


def test_load_config_uses_gh_auth_token_for_github_token(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("hud.auth.shutil.which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        "hud.auth.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "github-token\n", ""),
    )

    assert load_config(tmp_path / "missing.toml").github_token == "github-token"


def test_load_config_prefers_explicit_github_token_over_gh(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "explicit-token")
    monkeypatch.setattr("hud.auth.shutil.which", lambda name: "/usr/bin/gh")

    assert load_config(tmp_path / "missing.toml").github_token == "explicit-token"


def test_s3_log_url_uses_direct_s3() -> None:
    client = HudClient(HudConfig())

    assert client.s3_log_url(123) == {
        "url": "https://ossci-raw-job-status.s3.amazonaws.com/log/123"
    }


def test_artifacts_accepts_top_level_list(monkeypatch) -> None:
    monkeypatch.setattr(
        "hud.client.requests.get",
        lambda url, params, headers, impersonate, timeout: FakeResponse(
            200, [{"name": "artifact.zip"}]
        ),
    )

    assert HudClient(HudConfig(), RetryPolicy(attempts=1)).artifacts("s3", 123) == [
        {"name": "artifact.zip"}
    ]


def test_clickhouse_query_encodes_parameters(monkeypatch) -> None:
    captured = {}

    def fake_get(url, params, headers, impersonate, timeout):
        captured.update(
            {
                "url": url,
                "params": params,
                "headers": headers,
                "impersonate": impersonate,
                "timeout": timeout,
            }
        )
        return FakeResponse(200, {"ok": True})

    monkeypatch.setattr("hud.client.requests.get", fake_get)

    result = HudClient(HudConfig(), RetryPolicy(attempts=1)).clickhouse_query(
        "queued_jobs", {"limit": 10}
    )

    assert result == {"ok": True}
    assert captured["url"].endswith("/clickhouse/queued_jobs")
    assert captured["params"] == {"parameters": '{"limit": 10}'}
    assert captured["impersonate"] == "chrome"


def test_similar_failures_builds_search_params(monkeypatch) -> None:
    captured = {}

    def fake_get(url, params, headers, impersonate, timeout):
        captured.update({"url": url, "params": params})
        return FakeResponse(200, {"matches": []})

    monkeypatch.setattr("hud.client.requests.get", fake_get)

    result = HudClient(HudConfig(), RetryPolicy(attempts=1)).similar_failures(
        "CUDA out of memory",
        repo="pytorch/pytorch",
        workflow_name="linux",
        branch_name="main",
        start_date="2026-06-01T00:00:00Z",
        end_date="2026-06-05T00:00:00Z",
        min_score=2.0,
    )

    assert result == {"matches": []}
    assert captured["url"].endswith("/search")
    assert captured["params"] == {
        "failure": "CUDA out of memory",
        "repo": "pytorch/pytorch",
        "workflowName": "linux",
        "branchName": "main",
        "startDate": "2026-06-01T00:00:00Z",
        "endDate": "2026-06-05T00:00:00Z",
        "minScore": 2.0,
    }


def test_429_has_friendly_error(monkeypatch) -> None:
    def fake_get(url, params, headers, impersonate, timeout):
        return FakeResponse(429)

    monkeypatch.setattr("hud.client.requests.get", fake_get)

    with pytest.raises(HudError, match="rate-limited"):
        HudClient(HudConfig(), RetryPolicy(attempts=1)).hud_data(
            "pytorch", "pytorch", "main"
        )
