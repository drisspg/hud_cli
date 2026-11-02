"""Build Perfetto-compatible (Chrome JSON) timelines of where one CI job spends its time.

Sources, all already collected by HUD:
- `default.workflow_job`: queue time (created_at -> started_at) and GitHub step spans.
- `ci.runs` + `ci.tests`: per-test-case spans, grouped into per-file report spans.
- The raw job log (see `parse_log`): composite-action sections, `set -x` commands, run_test.py
  test processes, dynamo benchmark models, and log silences.
- GitHub sources at the job's commit (see `with_links`) for clickable `source` args.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

MIN_GAP_MS = 1000
MIN_COMMAND_MS = 500
MIN_SILENCE_MS = 30_000
MAX_NAME = 120
MAX_ARG = 2000
LOG_TIMESTAMP = re.compile(r"^\ufeff?(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z ?")
# run_test.py names a process `file i/n`, or `file, <class filter> i/n` when classes are selected.
LOG_SHARD = re.compile(r"^(Running|Finished) (.+? \d+/\d+) \.\.\. \[")
LOG_XTRACE = re.compile(r"^(\++) (.*)")
LOG_MODEL = re.compile(r"^(cuda|cpu|xpu|mps) +(train|eval|inference) +(\S+)$")
LOG_MODEL_RESULT = re.compile(r"^(?:[a-z][a-z0-9_]*|\d+(?:\.\d+)?x)$")
LOG_MODEL_LOADING = "loading model: 0it [00:00"
GITHUB_REPO = "https://github.com/pytorch/pytorch"
TORCHBENCH_REPO = "https://github.com/pytorch/benchmark"
TORCHBENCH_PIN = ".ci/docker/ci_commit_pins/torchbench.txt"
BENCHMARK_MODEL_LISTS = {
    "huggingface.py": "benchmarks/dynamo/huggingface_models_list.txt",
    "timm_models.py": "benchmarks/dynamo/timm_models_list.txt",
}
PY_DEF = re.compile(r"^(\s*)(?:async\s+)?def\s+(test\w*)\s*\(", re.MULTILINE)
DEVICE_SUFFIX = re.compile(r"|CPU|CUDA|XPU|MPS|HPU|LAZY|META|PRIVATEUSE1")
PY_CLASS = re.compile(r"^class\s+(\w+)", re.MULTILINE)
SCRIPT_PATH = re.compile(r"(?:^|\s)(?:\./)?((?:\.ci|\.github|benchmarks|test|tools|scripts)/[\w./-]+\.(?:py|sh))\b")

# Repo path -> file contents at the job's commit, or None when unavailable.
SourceFetcher = Callable[[str], "str | None"]


@dataclass(frozen=True)
class Span:
    name: str
    start_ms: int
    end_ms: int
    args: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True)
class JobTimeline:
    job: dict[str, Any]
    steps: list[Span]
    tests: list[Span]
    log: LogSpans
    truncated: bool = False


def job_sql(job_id: int) -> str:
    return f"""
SELECT id, name, workflow_name, html_url, head_sha, head_branch, runner_name, conclusion,
       toUnixTimestamp64Milli(created_at) AS created_ms,
       toUnixTimestamp64Milli(started_at) AS started_ms,
       -- In-progress jobs have an epoch completed_at; treat them as running until now.
       toUnixTimestamp64Milli(if(completed_at < started_at, now64(9), completed_at)) AS completed_ms
FROM default.workflow_job FINAL
WHERE id = {int(job_id)}
LIMIT 1
"""


def steps_sql(job_id: int) -> str:
    return f"""
SELECT tupleElement(s, 'number') AS number, tupleElement(s, 'name') AS name,
       tupleElement(s, 'conclusion') AS conclusion,
       toUnixTimestamp64Milli(tupleElement(s, 'started_at')) AS start_ms,
       toUnixTimestamp64Milli(tupleElement(s, 'completed_at')) AS end_ms
FROM default.workflow_job FINAL
ARRAY JOIN steps AS s
WHERE id = {int(job_id)} AND tupleElement(s, 'started_at') > toDateTime('2000-01-01')
ORDER BY number
"""


def tests_sql(job_id: int, started_ms: int, completed_ms: int, limit: int) -> str:
    """Test cases of one job; the started_at bound keeps the 10B-row ci.runs scan narrow."""
    hour_ms = 3_600_000
    return f"""
WITH r AS (
    SELECT tests_id, report_uuid, rerun_number, outcome,
           toUnixTimestamp64Milli(started_at) AS start_ms,
           toUnixTimestamp64Milli(ended_at) AS end_ms
    FROM ci.runs
    WHERE github_workflow_job_id = {int(job_id)}
      AND started_at BETWEEN fromUnixTimestamp64Milli({int(started_ms) - hour_ms})
                         AND fromUnixTimestamp64Milli({int(completed_ms) + hour_ms})
)
SELECT t.file AS file, t.suite AS suite, t.case_name AS case_name,
       toString(r.report_uuid) AS report, r.rerun_number AS rerun, toString(r.outcome) AS outcome,
       r.start_ms AS start_ms, r.end_ms AS end_ms
FROM r
JOIN (SELECT id, file, suite, case_name FROM ci.tests FINAL WHERE id IN (SELECT tests_id FROM r)) AS t
  ON t.id = r.tests_id
ORDER BY start_ms
LIMIT {int(limit) + 1}
"""


def step_spans(rows: Iterable[dict[str, Any]]) -> list[Span]:
    return [
        Span(row["name"], int(row["start_ms"]), max(int(row["end_ms"]), int(row["start_ms"])), {"conclusion": row["conclusion"], "number": row["number"]})
        for row in rows
    ]


def test_spans(rows: Iterable[dict[str, Any]]) -> list[Span]:
    spans = []
    for row in rows:
        name = f"{row['suite']}.{row['case_name']}" if row["suite"] else row["case_name"]
        args = {key: row[key] for key in ("file", "suite", "outcome", "rerun", "report")}
        spans.append(Span(name, int(row["start_ms"]), max(int(row["end_ms"]), int(row["start_ms"])), args))
    return spans


def parse_log_timestamp(match: re.Match[str]) -> int:
    seconds = datetime.strptime(match[1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    fraction = float(f"0.{match[2]}") if match[2] else 0.0
    return round((seconds + fraction) * 1000)


@dataclass(frozen=True)
class LogSpans:
    """Spans recovered from one raw job log; every log line carries a UTC timestamp."""

    actions: list[Span] = field(default_factory=list)
    commands: list[Span] = field(default_factory=list)
    processes: list[Span] = field(default_factory=list)
    models: list[Span] = field(default_factory=list)
    silences: list[Span] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(len(spans) for spans in (self.actions, self.commands, self.processes, self.models, self.silences))


def parse_log(text: str, min_command_ms: int = MIN_COMMAND_MS, min_silence_ms: int = MIN_SILENCE_MS) -> LogSpans:
    """Recover sub-step spans from a raw GitHub Actions log in one pass.

    - actions: `##[group]Run X` headers, each running until the next one (composite-action granularity).
    - commands: `set -x` lines (`+ cmd`), each running until the next xtrace or runner line.
    - processes: run_test.py `Running X i/n` / `Finished X i/n` pairs, including import and collection.
    - models: dynamo benchmark `cuda train <model>` headers, until the next model or command.
    - silences: gaps of at least `min_silence_ms` between consecutive log lines (see `idle_silences`).
    Open spans (timeouts, crashes, cancellation) end at the last log line.
    """
    spans = LogSpans()
    open_action: tuple[str, int] | None = None
    open_command: tuple[str, int] | None = None
    open_model: tuple[str, int, dict[str, Any]] | None = None
    open_processes: dict[str, int] = {}
    loading_ms: int | None = None
    last_ms: int | None = None

    def close_command(end_ms: int) -> None:
        nonlocal open_command
        if open_command and end_ms - open_command[1] >= min_command_ms:
            command, start_ms = open_command
            spans.commands.append(Span(command[:MAX_NAME], start_ms, end_ms, {"command": command[:MAX_ARG]}))
        open_command = None

    def close_model(end_ms: int) -> None:
        nonlocal open_model
        if open_model:
            name, start_ms, args = open_model
            spans.models.append(Span(name, start_ms, end_ms, args))
        open_model = None

    for line in text.splitlines():
        match = LOG_TIMESTAMP.match(line)
        if not match:
            continue
        timestamp = parse_log_timestamp(match)
        content = line[match.end():].rstrip()
        if last_ms is not None and timestamp - last_ms >= min_silence_ms:
            spans.silences.append(Span("log silent", last_ms, timestamp))
        last_ms = timestamp
        if content.startswith("##[group]Run "):
            if open_action:
                spans.actions.append(Span(open_action[0], open_action[1], timestamp))
            open_action = (content.removeprefix("##[group]")[:MAX_NAME], timestamp)
            close_command(timestamp)
            close_model(timestamp)
            loading_ms = None
        elif content.startswith("##[endgroup]"):
            close_command(timestamp)
        elif xtrace := LOG_XTRACE.match(content):
            close_command(timestamp)
            close_model(timestamp)
            loading_ms = None
            if xtrace[1] == "+":
                open_command = (xtrace[2], timestamp)
        elif model := LOG_MODEL.match(content):
            # The benchmark prints the model header after loading it; start at the loading bar.
            start_ms = loading_ms if loading_ms is not None else timestamp
            close_model(start_ms)
            args = {"command": open_command[0][:MAX_ARG]} if open_command else {}
            open_model = (f"{model[1]} {model[2]} {model[3]}", start_ms, args)
            loading_ms = None
        elif content.startswith(LOG_MODEL_LOADING):
            loading_ms = timestamp
        elif open_model and "result" not in open_model[2] and LOG_MODEL_RESULT.match(content):
            open_model[2]["result"] = content
        if shard := LOG_SHARD.search(content):
            kind, name = shard.groups()
            if kind == "Running":
                open_processes[name] = timestamp
            elif name in open_processes:
                spans.processes.append(Span(name, open_processes.pop(name), timestamp, {"finished": True}))
    if last_ms is not None:
        if open_action:
            spans.actions.append(Span(open_action[0], open_action[1], last_ms))
        close_command(last_ms)
        close_model(last_ms)
        spans.processes.extend(Span(name, start, last_ms, {"finished": False}) for name, start in open_processes.items())
    spans.processes.sort(key=lambda span: span.start_ms)
    return spans


def assign_lanes(spans: Iterable[Span]) -> list[tuple[int, Span]]:
    """Greedy interval partitioning so concurrent spans never share a Perfetto track."""
    lane_ends: list[int] = []
    placed = []
    for span in sorted(spans, key=lambda item: (item.start_ms, -item.end_ms)):
        lane = next((index for index, end in enumerate(lane_ends) if end <= span.start_ms), len(lane_ends))
        if lane == len(lane_ends):
            lane_ends.append(span.end_ms)
        else:
            lane_ends[lane] = span.end_ms
        placed.append((lane, span))
    return placed


def uncovered(
    window: Span, spans: Iterable[Span], min_gap_ms: int = MIN_GAP_MS, name: str = "no test case running"
) -> list[Span]:
    """Parts of `window` where none of `spans` is running, at least `min_gap_ms` long."""
    gaps = []
    cursor = window.start_ms
    for span in sorted(spans, key=lambda item: item.start_ms):
        if span.end_ms <= cursor:
            continue
        if span.start_ms - cursor >= min_gap_ms:
            gaps.append(Span(name, cursor, min(span.start_ms, window.end_ms), window.args))
        cursor = max(cursor, span.end_ms)
        if cursor >= window.end_ms:
            break
    if window.end_ms - cursor >= min_gap_ms:
        gaps.append(Span(name, cursor, window.end_ms, window.args))
    return [gap for gap in gaps if gap.duration_ms >= min_gap_ms]


def idle_silences(timeline: JobTimeline) -> list[Span]:
    """Log silences outside test processes, test cases, and benchmark models.

    run_test.py buffers each test process's output and prints it when the process finishes, and
    benchmark models compile quietly, so silence inside those spans is expected. Silence outside them
    is either a quiet command (`docker pull`, a cache copy), named in `during`, or real idle time.
    """
    log = timeline.log
    busy = sorted([*log.processes, *log.models, *timeline.tests], key=lambda span: span.start_ms)
    gaps = [gap for silence in log.silences for gap in uncovered(silence, busy, MIN_SILENCE_MS, "log silent outside tests")]

    def during(gap: Span) -> Span:
        command = next((c for c in log.commands if c.start_ms <= gap.start_ms < c.end_ms), None)
        return replace(gap, args=gap.args | ({"during": command.name} if command else {}))

    return [during(gap) for gap in gaps]


def file_spans(tests: Iterable[Span]) -> list[Span]:
    """One span per (file, report): a report is one test process's XML output."""
    groups: dict[tuple[str, str], list[Span]] = {}
    for test in tests:
        groups.setdefault((test.args["file"], test.args["report"]), []).append(test)
    spans = []
    for (file, report), members in groups.items():
        outcomes: dict[str, int] = {}
        for member in members:
            outcomes[member.args["outcome"]] = outcomes.get(member.args["outcome"], 0) + 1
        spans.append(
            Span(
                file,
                min(member.start_ms for member in members),
                max(member.end_ms for member in members),
                {"report": report, "cases": len(members), "test_case_sec": round(sum(m.duration_ms for m in members) / 1000, 1), **outcomes}
                | ({"source": source.split("#")[0]} if (source := members[0].args.get("source")) else {}),
            )
        )
    return sorted(spans, key=lambda span: span.start_ms)


def main_step(timeline: JobTimeline) -> Span | None:
    return next((step for step in timeline.steps if step.name in ("Test", "Build")), None)


def build_trace(timeline: JobTimeline) -> dict[str, Any]:
    job = timeline.job
    pid = 1
    events: list[dict[str, Any]] = [
        {"ph": "M", "pid": pid, "name": "process_name", "args": {"name": job["name"]}},
    ]
    thread_ids: dict[str, int] = {}

    def add(track: str, span: Span, category: str) -> None:
        if track not in thread_ids:
            tid = len(thread_ids) + 1
            thread_ids[track] = tid
            events.append({"ph": "M", "pid": pid, "tid": tid, "name": "thread_name", "args": {"name": track}})
            events.append({"ph": "M", "pid": pid, "tid": tid, "name": "thread_sort_index", "args": {"sort_index": tid}})
        events.append(
            {"ph": "X", "pid": pid, "tid": thread_ids[track], "name": span.name, "cat": category,
             "ts": span.start_ms * 1000, "dur": span.duration_ms * 1000, "args": span.args}
        )

    def add_lanes(track: str, spans: Iterable[Span], category: str) -> None:
        for lane, span in assign_lanes(spans):
            add(f"{track} {lane}", span, category)

    created, started, completed = (int(job[key]) for key in ("created_ms", "started_ms", "completed_ms"))
    if started > created:
        add("job", Span("queued", created, started), "job")
    add("job", Span("running", started, max(completed, started), {"runner": job["runner_name"], "conclusion": job["conclusion"]}), "job")
    for step in timeline.steps:
        add("steps", step, "step")
    log = timeline.log
    for action in log.actions:
        add("actions (log)", action, "action")
    for command in log.commands:
        add("shell commands (log)", command, "command")
    for silence in idle_silences(timeline):
        add("log silent outside tests >=30s", silence, "silence")
    add_lanes("test processes (log)", log.processes, "process")
    add_lanes("benchmark models (log)", log.models, "model")
    add_lanes("test files", file_spans(timeline.tests), "file")
    for gap in idle_gaps(timeline):
        add("idle in main step", gap, "gap")
    add_lanes("test cases", timeline.tests, "test")
    return {
        "traceEvents": events,
        "displayTimeUnit": "ms",
        "metadata": {key: job[key] for key in ("id", "name", "workflow_name", "html_url", "head_sha", "head_branch")}
        | {"truncated_tests": timeline.truncated},
    }


def idle_gaps(timeline: JobTimeline) -> list[Span]:
    """Main-step time with no test case running; empty when the job reported no test cases."""
    test_step = main_step(timeline)
    return uncovered(test_step, timeline.tests) if test_step and timeline.tests else []


def slowest(spans: Iterable[Span], top: int) -> list[dict[str, Any]]:
    return [
        {"name": span.name, "sec": round(span.duration_ms / 1000, 1)} | span.args
        for span in sorted(spans, key=lambda item: -item.duration_ms)[:top]
    ]


def summarize(timeline: JobTimeline, top: int = 10) -> dict[str, Any]:
    job = timeline.job
    test_step = main_step(timeline)
    log = timeline.log
    commands = [Span(span.name, span.start_ms, span.end_ms) for span in log.commands]
    return {
        "job_id": job["id"],
        "name": job["name"],
        "html_url": job["html_url"],
        "queue_sec": round(max(int(job["started_ms"]) - int(job["created_ms"]), 0) / 1000),
        "runtime_sec": round((int(job["completed_ms"]) - int(job["started_ms"])) / 1000),
        "main_step": test_step.name if test_step else None,
        "main_step_sec": round(test_step.duration_ms / 1000) if test_step else None,
        "main_step_idle_sec": round(sum(gap.duration_ms for gap in idle_gaps(timeline)) / 1000) if timeline.tests else None,
        "log_silent_outside_tests_sec": round(sum(span.duration_ms for span in idle_silences(timeline)) / 1000),
        "counts": {
            "steps": len(timeline.steps),
            "actions": len(log.actions),
            "shell_commands": len(log.commands),
            "test_processes": len(log.processes),
            "benchmark_models": len(log.models),
            "test_cases": len(timeline.tests),
        },
        "test_cases_truncated": timeline.truncated,
        "slowest_steps": slowest(timeline.steps, top),
        "slowest_commands": slowest(commands, top),
        "slowest_test_processes": slowest(log.processes, top),
        "slowest_models": slowest(log.models, top),
        "slowest_files": slowest(file_spans(timeline.tests), top),
    }


def blob_url(sha: str, path: str, line: int | None = None) -> str:
    return f"{GITHUB_REPO}/blob/{sha}/{path}" + (f"#L{line}" if line else "")


def find_test_defs(source: str) -> list[tuple[str | None, str, int]]:
    """(enclosing top-level class, test function name, 1-based line) for every `def test*`."""
    classes = [(match.start(), match[1]) for match in PY_CLASS.finditer(source)]
    definitions = []
    for match in PY_DEF.finditer(source):
        owner = next((name for start, name in reversed(classes) if start < match.start()), None) if match[1] else None
        definitions.append((owner, match[2], source.count("\n", 0, match.start(2)) + 1))
    return definitions


def find_test_line(definitions: list[tuple[str | None, str, int]], suite: str, case_name: str) -> int | None:
    """Line of the def that generated `suite.case_name`.

    Instantiated tests extend the def name (`test_add` -> `test_add_cuda_float32`) and the class
    name (`TestOps` -> `TestOpsCUDA`); prefer the longest matching def, then a matching class.
    """
    candidates = [
        (owner is not None and DEVICE_SUFFIX.fullmatch(suite.removeprefix(owner)) is not None and suite.startswith(owner), len(name), line)
        for owner, name, line in definitions
        if case_name == name or case_name.startswith(f"{name}_")
    ]
    return max(candidates)[2] if candidates else None


def benchmark_model_url(sha: str, model: Span, fetch: SourceFetcher) -> str | None:
    command = model.args.get("command", "")
    name = model.name.split()[-1]
    if "torchbench.py" in command:
        pin = fetch(TORCHBENCH_PIN)
        return f"{TORCHBENCH_REPO}/tree/{pin.strip()}/torchbenchmark/models/{name}" if pin else None
    for script, model_list in BENCHMARK_MODEL_LISTS.items():
        if script in command and (source := fetch(model_list)):
            line = next((index for index, row in enumerate(source.splitlines(), 1) if re.split(r"[ ,]", row)[0] == name), None)
            return blob_url(sha, model_list, line)
    return None


def with_links(timeline: JobTimeline, fetch: SourceFetcher) -> JobTimeline:
    """Add clickable `source` (GitHub at the job's commit) and `github_log` (step log) args."""
    sha = timeline.job["head_sha"]
    sources: dict[str, str | None] = {}

    def cached(path: str) -> str | None:
        if path not in sources:
            sources[path] = fetch(path)
        return sources[path]

    definitions: dict[str, list[tuple[str | None, str, int]]] = {}

    def test_source(file: str, suite: str, case_name: str) -> str | None:
        if not file.endswith(".py"):
            return None
        if file not in definitions:
            source = cached(file)
            definitions[file] = find_test_defs(source) if source else []
        return blob_url(sha, file, find_test_line(definitions[file], suite, case_name))

    def link(span: Span, **links: str | None) -> Span:
        return replace(span, args=span.args | {key: url for key, url in links.items() if url})

    def step_log(span: Span) -> str | None:
        step = next((step for step in reversed(timeline.steps) if step.start_ms <= span.start_ms), None)
        return f"{timeline.job['html_url']}#step:{step.args['number']}:1" if step else None

    def process_source(name: str) -> str | None:
        file = name.split(",")[0].split()[0]
        return None if file.startswith("cpp/") else blob_url(sha, f"test/{file}.py")

    def script(command: str) -> str | None:
        match = SCRIPT_PATH.search(command)
        return blob_url(sha, match[1]) if match else None

    tests = [
        link(test, source=test_source(test.args["file"], test.args["suite"], test.name.removeprefix(f"{test.args['suite']}.")))
        for test in timeline.tests
    ]
    log = timeline.log
    log = LogSpans(
        actions=[link(span, github_log=step_log(span)) for span in log.actions],
        commands=[link(span, source=script(span.args["command"]), github_log=step_log(span)) for span in log.commands],
        processes=[link(span, source=process_source(span.name), github_log=step_log(span)) for span in log.processes],
        models=[link(span, source=benchmark_model_url(sha, span, cached), github_log=step_log(span)) for span in log.models],
        silences=[link(span, github_log=step_log(span)) for span in log.silences],
    )
    steps = [link(step, github_log=f"{timeline.job['html_url']}#step:{step.args['number']}:1") for step in timeline.steps]
    return replace(timeline, steps=steps, tests=tests, log=log)


def source_paths(timeline: JobTimeline) -> set[str]:
    """Repo files `with_links` reads: Python test files plus benchmark pins and model lists."""
    paths = {test.args["file"] for test in timeline.tests if test.args["file"].endswith(".py")}
    commands = {model.args.get("command", "") for model in timeline.log.models}
    if any("torchbench.py" in command for command in commands):
        paths.add(TORCHBENCH_PIN)
    paths.update(path for script, path in BENCHMARK_MODEL_LISTS.items() if any(script in command for command in commands))
    return paths
