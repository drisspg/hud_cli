from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from curl_cffi import requests


class LogError(RuntimeError):
    pass


LOG_BASE_URL = "https://ossci-raw-job-status.s3.amazonaws.com/log"
DEFAULT_PATTERNS = {
    "error": r"(?i)error:",
    "exception": r"(?i)exception:",
    "warning": r"(?i)warning:",
    "test_failed": r"FAILED.*test_",
    "cuda_error": r"CUDA error|CUDA exception|cudaError",
    "out_of_memory": r"OutOfMemoryError|OOM|out of memory",
    "build_failed": r"Build failed|compilation failed|error: command .* failed",
}


@dataclass(frozen=True)
class LogMatch:
    line_number: int
    text: str


@dataclass(frozen=True)
class LogSection:
    start_line: int
    content: str
    truncated: bool


def s3_log_url(job_id: int) -> str:
    return f"{LOG_BASE_URL}/{job_id}"


def download_log(job_id: int, destination: Path) -> Path:
    response = requests.get(s3_log_url(job_id), impersonate="chrome", timeout=60)
    if response.status_code == 404:
        raise LogError(f"No raw S3 log found for job {job_id}")
    if response.status_code == 429:
        raise LogError(f"S3 log request for job {job_id} was rate-limited")
    response.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(response.text)
    return destination


def search_text(text: str, pattern: str, limit: int = 50) -> list[LogMatch]:
    regex = re.compile(pattern, re.IGNORECASE)
    matches = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if regex.search(line):
            matches.append(LogMatch(line_number, line))
        if len(matches) >= limit:
            break
    return matches


def search_file(path: Path, pattern: str, limit: int = 50) -> list[LogMatch]:
    return search_text(path.read_text(errors="replace"), pattern, limit)


def extract_patterns(path: Path, patterns: dict[str, str] | None = None) -> dict[str, Any]:
    compiled_patterns = {
        name: re.compile(pattern) for name, pattern in (patterns or DEFAULT_PATTERNS).items()
    }
    results: dict[str, Any] = {
        "path": str(path),
        "counts": {},
        "samples": {},
    }
    for line_number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        for name, pattern in compiled_patterns.items():
            match = pattern.search(line)
            if not match:
                continue
            results["counts"][name] = results["counts"].get(name, 0) + 1
            samples = results["samples"].setdefault(name, [])
            if len(samples) < 5:
                samples.append(
                    {
                        "line_number": line_number,
                        "text": line[:150],
                        "groups": list(match.groups()) or None,
                    }
                )
    return results


def extract_test_results(path: Path, failure_limit: int = 20) -> dict[str, Any]:
    lines = path.read_text(errors="replace").splitlines()
    results: dict[str, Any] = {
        "path": str(path),
        "test_counts": {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
        "failed_tests": [],
        "duration_seconds": None,
    }
    pytest_summary = re.compile(r"=+\s+(?P<summary>.*?)\s+=+")
    unittest_summary = re.compile(r"Ran (?P<total>\d+) tests in (?P<duration>[\d.]+)s")
    failure_patterns = [
        re.compile(r"FAIL: (?P<name>test\w+)"),
        re.compile(r"ERROR: (?P<name>test\w+)"),
        re.compile(r"FAILED (?P<name>\S+::\S+)(?:\s|$)"),
    ]
    for line_number, line in enumerate(lines, start=1):
        parse_pytest_summary(line, pytest_summary, results)
        parse_unittest_summary(line, unittest_summary, results)
        append_failure(lines, line_number, line, failure_patterns, results, failure_limit)
    return results


def filter_sections(
    path: Path,
    start_pattern: str,
    end_pattern: str | None = None,
    max_lines: int = 100,
) -> list[LogSection]:
    start_re = re.compile(start_pattern)
    end_re = re.compile(end_pattern) if end_pattern else None
    sections: list[LogSection] = []
    in_section = False
    current_section: list[str] = []
    current_start_line = 0
    for line_number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        if not in_section and start_re.search(line):
            in_section = True
            current_section = [line]
            current_start_line = line_number
            continue
        if not in_section:
            continue
        if len(current_section) >= max_lines:
            current_section.append(f"... [truncated after {max_lines} lines] ...")
            sections.append(LogSection(current_start_line, "\n".join(current_section), True))
            in_section = False
            current_section = []
            continue
        current_section.append(line)
        if end_re and end_re.search(line):
            sections.append(LogSection(current_start_line, "\n".join(current_section), False))
            in_section = False
            current_section = []
    if in_section and current_section:
        sections.append(LogSection(current_start_line, "\n".join(current_section), False))
    return sections


def parse_pytest_summary(line: str, pattern: re.Pattern, results: dict[str, Any]) -> None:
    match = pattern.search(line)
    if not match:
        return
    summary = match.group("summary")
    counts = results["test_counts"]
    matched_count = False
    for name in ["failed", "passed", "skipped"]:
        count_match = re.search(rf"(\d+) {name}", summary)
        if count_match:
            counts[name] = int(count_match.group(1))
            matched_count = True
    if not matched_count:
        return
    counts["total"] = counts["failed"] + counts["passed"] + counts["skipped"]
    duration_match = re.search(r"in ([\d.]+)s", summary)
    if duration_match:
        results["duration_seconds"] = float(duration_match.group(1))


def parse_unittest_summary(line: str, pattern: re.Pattern, results: dict[str, Any]) -> None:
    match = pattern.search(line)
    if not match:
        return
    results["test_counts"]["total"] = int(match.group("total"))
    results["duration_seconds"] = float(match.group("duration"))


def append_failure(
    lines: list[str],
    line_number: int,
    line: str,
    patterns: list[re.Pattern],
    results: dict[str, Any],
    failure_limit: int,
) -> None:
    if len(results["failed_tests"]) >= failure_limit:
        return
    for pattern in patterns:
        match = pattern.search(line)
        if not match:
            continue
        results["failed_tests"].append(
            {
                "test_name": match.group("name"),
                "line_number": line_number,
                "context": lines[line_number - 1 : line_number + 4],
            }
        )
        return
