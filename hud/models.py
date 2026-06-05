from __future__ import annotations

import re
from typing import Any


def enrich_job(job: dict[str, Any], job_names: list[Any]) -> dict[str, Any]:
    enriched = dict(job)
    job_id = enriched.get("id")
    if isinstance(job_id, int) and 0 <= job_id < len(job_names):
        enriched["name"] = str(job_names[job_id])
    elif not enriched.get("name") and enriched.get("htmlUrl"):
        enriched["name"] = str(enriched["htmlUrl"]).rstrip("/").split("/")[-1]
    return enriched


def filtered_commits(
    data: dict[str, Any],
    include_success: bool = False,
    include_pending: bool = False,
    include_failures: bool = False,
    job_regex: str | None = None,
    failure_regex: str | None = None,
) -> list[dict[str, Any]]:
    job_names = data.get("jobNames", [])
    job_pattern = re.compile(job_regex, re.IGNORECASE) if job_regex else None
    failure_pattern = re.compile(failure_regex, re.IGNORECASE) if failure_regex else None
    commits = []
    for commit in data.get("shaGrid", []):
        jobs = [enrich_job(job, job_names) for job in commit.get("jobs", []) if job]
        selected_jobs = [
            job
            for job in jobs
            if job_matches(job, include_success, include_pending, include_failures)
            and regex_matches(job, job_pattern, failure_pattern)
        ]
        commits.append(normalize_commit(commit, jobs, selected_jobs))
    return commits


def normalize_commit(
    commit: dict[str, Any], jobs: list[dict[str, Any]], selected_jobs: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "sha": commit.get("sha", ""),
        "short_sha": str(commit.get("sha", ""))[:10],
        "title": commit.get("commitTitle", ""),
        "author": commit.get("author", ""),
        "time": commit.get("time", ""),
        "pr": commit.get("prNum"),
        "job_counts": job_counts(jobs),
        "jobs": selected_jobs,
    }


def job_counts(jobs: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": 0, "success": 0, "failure": 0, "pending": 0, "skipped": 0}
    for job in jobs:
        counts["total"] += 1
        conclusion = str(job.get("conclusion") or "").lower()
        status = str(job.get("status") or "").lower()
        if conclusion in counts:
            counts[conclusion] += 1
        elif status in {"queued", "pending", "in_progress"}:
            counts["pending"] += 1
    return counts


def job_matches(
    job: dict[str, Any],
    include_success: bool,
    include_pending: bool,
    include_failures: bool,
) -> bool:
    if not any([include_success, include_pending, include_failures]):
        return False
    conclusion = str(job.get("conclusion") or "").lower()
    status = str(job.get("status") or "").lower()
    if include_success and conclusion == "success":
        return True
    if include_failures and conclusion == "failure":
        return True
    return include_pending and status in {"queued", "pending", "in_progress"}


def regex_matches(
    job: dict[str, Any], job_pattern: re.Pattern | None, failure_pattern: re.Pattern | None
) -> bool:
    if job_pattern and not job_pattern.search(str(job.get("name") or job.get("htmlUrl") or "")):
        return False
    return not (
        failure_pattern
        and not failure_pattern.search(str(job.get("failureLine") or job.get("failureCaptures") or ""))
    )
