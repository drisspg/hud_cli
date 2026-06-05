from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from curl_cffi import requests

from hud.auth import HudConfig
from hud.logs import s3_log_url


class HudError(RuntimeError):
    pass


class HudAuthError(HudError):
    pass


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    delay_seconds: float = 0.5


class HudClient:
    def __init__(self, config: HudConfig, retry_policy: RetryPolicy | None = None) -> None:
        self.config = config
        self.retry_policy = retry_policy or RetryPolicy()

    def hud_data(
        self,
        repo_owner: str,
        repo_name: str,
        branch_or_sha: str,
        page: int = 1,
        per_page: int = 20,
        merge_landing_flow: bool = True,
    ) -> dict[str, Any]:
        return self.get(
            f"hud/{repo_owner}/{repo_name}/{branch_or_sha}/{page}",
            {
                "per_page": per_page,
                "mergeLF": str(merge_landing_flow).lower(),
            },
        )

    def job(self, job_id: int) -> dict[str, Any]:
        return self.get(f"job/{job_id}")

    def artifacts(self, provider: str, job_id: int) -> Any:
        return self.request_json(f"artifacts/{provider}/{job_id}")

    def s3_log_url(self, job_id: int) -> dict[str, str]:
        return {"url": s3_log_url(job_id)}

    def clickhouse_query(
        self, query_name: str, parameters: dict[str, Any] | None = None
    ) -> Any:
        params: dict[str, str] = {}
        if parameters:
            params["parameters"] = json.dumps(parameters)
        return self.request_json(f"clickhouse/{query_name}", params)

    def similar_failures(
        self,
        failure: str,
        repo: str | None = None,
        workflow_name: str | None = None,
        branch_name: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        min_score: float = 1.0,
        days: int = 7,
    ) -> dict[str, Any]:
        if start_date is None or end_date is None:
            stop_time = datetime.now(UTC).replace(microsecond=0)
            end_date = end_date or stop_time.isoformat().replace("+00:00", "Z")
            start_date = start_date or (stop_time - timedelta(days=days)).isoformat().replace("+00:00", "Z")
        params: dict[str, Any] = {
            "failure": failure,
            "startDate": start_date,
            "endDate": end_date,
            "minScore": min_score,
        }
        if repo:
            params["repo"] = repo
        if workflow_name:
            params["workflowName"] = workflow_name
        if branch_name:
            params["branchName"] = branch_name
        return self.get("search", params)

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.request_json(endpoint, params)
        if not isinstance(response, dict):
            raise HudError(f"HUD returned {type(response).__name__}; expected a JSON object")
        return response

    def request_json(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        return self._request(
            f"{self.config.base_url.rstrip('/')}/{endpoint.lstrip('/')}", params or {}
        )

    def _request(self, url: str, params: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retry_policy.attempts):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=self._headers(),
                    impersonate="chrome",
                    timeout=30,
                )
                if response.status_code in {401, 403}:
                    raise HudAuthError(_auth_error_message(response.status_code, url))
                if response.status_code == 429:
                    raise HudError(
                        f"HUD rate-limited this request for {url}. Run `hud doctor` to inspect auth, then set HUD_INTERNAL_BOT_TOKEN or GITHUB_TOKEN if you have access."
                    )
                response.raise_for_status()
                return response.json()
            except HudAuthError:
                raise
            except requests.RequestsError as error:
                last_error = error
                if attempt + 1 == self.retry_policy.attempts:
                    break
                time.sleep(self.retry_policy.delay_seconds * (2**attempt))
            except json.JSONDecodeError as error:
                raise HudError(f"HUD returned invalid JSON for {url}: {error}") from error
        raise HudError(f"HUD request failed for {url}: {last_error}") from last_error

    def _headers(self) -> dict[str, str]:
        headers = {"accept": "application/json", "user-agent": "pytorch-hud-cli"}
        if self.config.internal_bot_token:
            headers["x-hud-internal-bot"] = self.config.internal_bot_token
        if self.config.github_token:
            headers["authorization"] = f"Bearer {self.config.github_token}"
        return headers


def _auth_error_message(status_code: int, url: str) -> str:
    return (
        f"HUD request got HTTP {status_code} for {url}. Try again on the corporate VPN, "
        "set HUD_INTERNAL_BOT_TOKEN if you have one, or use the planned SSO proxy once deployed."
    )
