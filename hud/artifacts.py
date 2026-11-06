"""Per-test-case rows from a job's GitHub Actions test-reports artifact (pytest JUnit XML).

Some workflows (XPU, for example) upload `test-reports-*_<job_id>.zip` but never reach `ci.runs`. JUnit
XML records each test suite's start timestamp and each case's duration in execution order, so cases
are laid out back to back from the suite start. Suites whose case durations sum past their wall time
ran under pytest-xdist; their cases are packed greedily onto that many workers, which estimates
start times rather than recording them.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree

GITHUB_REPO_API = "repos/pytorch/pytorch"
MAX_ARTIFACT_BYTES = 200 * 1024 * 1024
# Back-to-back cases leave small gaps, so serial suites sum to slightly under their wall time.
PARALLEL_RATIO = 1.05


class ArtifactError(RuntimeError):
    pass


def gh_api(path: str, *args: str) -> bytes:
    """`gh api` output; gh handles auth and the GitHub transport that direct requests can be denied."""
    if shutil.which("gh") is None:
        raise ArtifactError("GitHub CLI `gh` is not installed")
    result = subprocess.run(["gh", "api", path, *args], capture_output=True, timeout=300, check=False)
    if result.returncode != 0:
        raise ArtifactError(f"gh api {path} failed: {result.stderr.decode(errors='replace').strip()[:300]}")
    return result.stdout


def fetch_test_reports(run_id: int, job_id: int) -> bytes | None:
    """The job's test-reports zip, or None when the run has none for it (e.g. the job timed out)."""
    suffix = f"_{job_id}.zip"
    listing = gh_api(
        f"{GITHUB_REPO_API}/actions/runs/{int(run_id)}/artifacts?per_page=100",
        "--paginate",
        "--jq",
        ".artifacts[] | select(.expired | not) | [.id, .name, .size_in_bytes] | @tsv",
    )
    for line in listing.decode().splitlines():
        artifact_id, name, size = line.split("\t")
        if name.startswith("test-reports-") and name.endswith(suffix):
            if int(size) > MAX_ARTIFACT_BYTES:
                raise ArtifactError(f"{name} is {size} bytes, over the {MAX_ARTIFACT_BYTES} cap")
            return gh_api(f"{GITHUB_REPO_API}/actions/artifacts/{artifact_id}/zip")
    return None


def outcome(case: ElementTree.Element) -> str:
    for tag in ("failure", "error", "skipped"):
        if case.find(tag) is not None:
            return {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
    return "passed"


def suite_rows(suite: ElementTree.Element, report: str) -> list[dict[str, Any]]:
    cases = list(suite.iter("testcase"))
    if not cases or not suite.get("timestamp"):
        return []
    start_ms = round(datetime.fromisoformat(suite.get("timestamp")).replace(tzinfo=timezone.utc).timestamp() * 1000)
    durations = [round(float(case.get("time") or 0) * 1000) for case in cases]
    wall_ms = round(float(suite.get("time") or 0) * 1000)
    workers = max(1, round(sum(durations) / wall_ms)) if wall_ms and sum(durations) > wall_ms * PARALLEL_RATIO else 1
    free_ms = [start_ms] * workers
    rows = []
    for case, duration_ms in zip(cases, durations, strict=True):
        worker = min(range(workers), key=free_ms.__getitem__)
        case_start = free_ms[worker]
        free_ms[worker] = case_start + duration_ms
        file = case.get("file") or ""
        rows.append(
            {
                "file": file if file.startswith("test/") or not file else f"test/{file}",
                "suite": case.get("classname") or "",
                "case_name": case.get("name") or "",
                "report": report,
                "rerun": 0,
                "outcome": outcome(case),
                "start_ms": case_start,
                "end_ms": case_start + duration_ms,
            }
        )
    return rows


def report_rows(archive: bytes) -> list[dict[str, Any]]:
    """Test-case rows (the `ci.runs` columns `test_spans` reads) from every JUnit XML in the zip."""
    rows = []
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for name in zipped.namelist():
            if name.endswith(".xml") and "/python-pytest/" in f"/{name}":
                root = ElementTree.fromstring(zipped.read(name))
                for suite in root.iter("testsuite"):
                    rows.extend(suite_rows(suite, PurePosixPath(name).stem))
    return sorted(rows, key=lambda row: row["start_ms"])
