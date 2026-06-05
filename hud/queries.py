from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from curl_cffi import requests


@dataclass(frozen=True)
class QueryInfo:
    name: str
    description: str
    example_params: dict[str, Any] = field(default_factory=dict)


TEST_INFRA_RAW_BASE = "https://raw.githubusercontent.com/pytorch/test-infra/main/torchci/clickhouse_queries"


class QuerySourceError(RuntimeError):
    pass


QUERY_CATALOG: dict[str, QueryInfo] = {
    "queued_jobs": QueryInfo("queued_jobs", "Currently queued PyTorch CI jobs."),
    "master_commit_red": QueryInfo(
        "master_commit_red",
        "Historical trunk red/green/pending status by time bucket.",
        {
            "startTime": "2026-06-01T00:00:00",
            "stopTime": "2026-06-05T00:00:00",
            "timezone": "America/Los_Angeles",
            "granularity": "day",
            "usePercentage": True,
        },
    ),
    "disabled_test_historical": QueryInfo(
        "disabled_test_historical",
        "Historical disabled/skipped test counts.",
        {
            "startTime": "2026-06-01T00:00:00",
            "stopTime": "2026-06-05T00:00:00",
            "label": "skipped",
            "repo": "pytorch/pytorch",
            "state": "open",
            "platform": "",
            "triaged": "",
            "granularity": "day",
        },
    ),
    "flaky_tests/across_jobs": QueryInfo(
        "flaky_tests/across_jobs",
        "Flaky test data across jobs for a bounded time window.",
        {
            "startTime": "2026-06-01T00:00:00",
            "stopTime": "2026-06-05T00:00:00",
        },
    ),
    "test_time_per_file": QueryInfo(
        "test_time_per_file",
        "Average test runtime by invoking file over recent viable/strict commits.",
    ),
    "test_time_per_file_periodic_jobs": QueryInfo(
        "test_time_per_file_periodic_jobs",
        "Average test runtime by invoking file over recent successful periodic jobs.",
    ),
    "tts_avg": QueryInfo("tts_avg", "Average time-to-signal metrics."),
    "tts_percentile": QueryInfo("tts_percentile", "Percentile time-to-signal metrics."),
    "ttrs_percentiles": QueryInfo("ttrs_percentiles", "Time-to-revert-signal percentiles."),
}


def time_window(days: int) -> tuple[str, str]:
    stop_time = datetime.now(UTC).replace(microsecond=0)
    start_time = stop_time - timedelta(days=days)
    return start_time.isoformat().replace("+00:00", "Z"), stop_time.isoformat().replace("+00:00", "Z")


def window_params(days: int) -> dict[str, str]:
    start_time, stop_time = time_window(days)
    return {"startTime": start_time, "stopTime": stop_time}


def fetch_query_source(query_name: str, filename: str) -> str:
    if filename not in {"query.sql", "params.json"}:
        raise QuerySourceError(f"unsupported query source file: {filename}")
    url = f"{TEST_INFRA_RAW_BASE}/{query_name}/{filename}"
    response = requests.get(url, impersonate="chrome", timeout=30)
    if response.status_code == 404:
        raise QuerySourceError(f"No {filename} found for query {query_name}")
    if response.status_code == 429:
        raise QuerySourceError(f"GitHub rate-limited query source request for {query_name}")
    response.raise_for_status()
    return response.text
