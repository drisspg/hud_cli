import json

from typer.testing import CliRunner

import hud.cli as cli
from hud.cli import app
from hud.trace import (
    JobTimeline,
    LogSpans,
    Span,
    assign_lanes,
    find_test_defs,
    find_test_line,
    idle_silences,
    parse_log,
    uncovered,
    with_links,
)

runner = CliRunner()


def frame(rows):
    columns = list(rows[0]) if rows else []
    values = [[row[column] for row in rows] for column in columns]
    return {"results": {"A": {"frames": [{"schema": {"fields": [{"name": c} for c in columns]}, "data": {"values": values}}]}}}


JOB = {
    "id": 7, "name": "linux / test (default, 1, 2, gpu)", "workflow_name": "trunk",
    "html_url": "https://github.com/x/job/7", "head_sha": "abc", "head_branch": "main",
    "runner_name": "r1", "conclusion": "success",
    "created_ms": 0, "started_ms": 10_000, "completed_ms": 100_000,
}
STEPS = [
    {"number": 1, "name": "Set up job", "conclusion": "success", "start_ms": 10_000, "end_ms": 20_000},
    {"number": 2, "name": "Test", "conclusion": "success", "start_ms": 20_000, "end_ms": 90_000},
]
TESTS = [
    {"file": "test/a.py", "suite": "A", "case_name": "test_x", "report": "r1", "rerun": 0, "outcome": "passed", "start_ms": 30_000, "end_ms": 40_000},
    {"file": "test/a.py", "suite": "A", "case_name": "test_y", "report": "r1", "rerun": 0, "outcome": "failed", "start_ms": 35_000, "end_ms": 50_000},
    {"file": "test/b.py", "suite": "B", "case_name": "test_z", "report": "r2", "rerun": 0, "outcome": "passed", "start_ms": 60_000, "end_ms": 70_000},
]


def fake_query(config, sql, settings=None):
    if "ARRAY JOIN steps" in sql:
        return frame(STEPS)
    if "ci.runs" in sql:
        return frame(TESTS)
    return frame([JOB])


def test_parse_log_pairs_running_and_finished_and_keeps_unfinished() -> None:
    log = "\n".join(
        [
            "\ufeff2026-07-02T13:49:36.5000000Z Running inductor/test_a 1/2 ... [2026-07-02 13:49:36.51][1.0]",
            "2026-07-02T13:49:37.0000000Z Running inductor/test_b 1/1 ... [2026-07-02 13:49:37.00][1.0]",
            "2026-07-02T13:50:36.5000000Z Finished inductor/test_a 1/2 ... [2026-07-02 13:50:36.51][2.0], took 1.00min",
            "2026-07-02T13:51:00.0000000Z ##[error]The operation was canceled.",
        ]
    )

    shards = {span.name: span for span in parse_log(log).processes}

    assert shards["inductor/test_a 1/2"].duration_ms == 60_000
    assert shards["inductor/test_a 1/2"].args == {"finished": True}
    assert shards["inductor/test_b 1/1"].duration_ms == 83_000
    assert shards["inductor/test_b 1/1"].args == {"finished": False}


def test_parse_log_recovers_actions_commands_models_and_silences() -> None:
    log = "\n".join(
        [
            "2026-11-01T07:00:00.0000000Z ##[group]Run .ci/pytorch/test.sh",
            "2026-11-01T07:00:00.1000000Z ##[endgroup]",
            "2026-11-01T07:00:01.0000000Z + pip install torch.whl",
            "2026-11-01T07:00:11.0000000Z ++ echo quick",
            "2026-11-01T07:00:11.1000000Z + python benchmarks/dynamo/torchbench.py --training",
            "2026-11-01T07:00:12.0000000Z cuda train alexnet                   ",
            "2026-11-01T07:00:20.0000000Z pass",
            "2026-11-01T07:00:58.0000000Z loading model: 0it [00:00, ?it/s]",
            "2026-11-01T07:01:00.0000000Z cuda eval  sam",
            "2026-11-01T07:01:00.5000000Z loading model: 0it [00:02, ?it/s]",
            "2026-11-01T07:01:04.0000000Z 1.379x",
            "2026-11-01T07:01:05.0000000Z eager_fail_to_run",
            "2026-11-01T07:01:06.0000000Z ##[group]Run upload",
        ]
    )

    spans = parse_log(log)

    assert [(s.name, s.duration_ms) for s in spans.actions] == [("Run .ci/pytorch/test.sh", 66_000), ("Run upload", 0)]
    assert [(s.name, s.duration_ms) for s in spans.commands] == [
        ("pip install torch.whl", 10_000),
        ("python benchmarks/dynamo/torchbench.py --training", 54_900),
    ]
    assert [(s.name, s.duration_ms, s.args) for s in spans.models] == [
        ("cuda train alexnet", 46_000, {"command": "python benchmarks/dynamo/torchbench.py --training", "result": "pass"}),
        ("cuda eval sam", 8_000, {"command": "python benchmarks/dynamo/torchbench.py --training", "result": "1.379x"}),
    ]
    assert [(s.start_ms % 3_600_000, s.duration_ms) for s in spans.silences] == [(20_000, 38_000)]


def test_assign_lanes_separates_overlapping_spans_and_reuses_free_lanes() -> None:
    lanes = {span.name: lane for lane, span in assign_lanes([Span("a", 0, 10), Span("b", 5, 15), Span("c", 10, 20)])}

    assert lanes == {"a": 0, "b": 1, "c": 0}


def test_uncovered_reports_gaps_between_overlapping_spans() -> None:
    gaps = uncovered(Span("Test", 0, 10_000), [Span("a", 2_000, 4_000), Span("b", 3_000, 5_000), Span("c", 5_500, 9_500)])

    assert [(gap.start_ms, gap.end_ms) for gap in gaps] == [(0, 2_000)]


def test_trace_writes_perfetto_events_and_summary(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "clickhouse_query", fake_query)
    monkeypatch.setattr(cli, "fetch_repo_files", lambda sha, paths: {"test/a.py": "class A:\n    def test_y(self):\n        pass\n"})
    output = tmp_path / "job.trace.json"

    result = runner.invoke(app, ["trace", "7", "--no-log", "-o", str(output), "--json"])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["queue_sec"] == 10
    assert summary["main_step_sec"] == 70
    assert summary["main_step_idle_sec"] == 40
    assert summary["slowest_files"][0] | {"report": None} == {"name": "test/a.py", "sec": 20.0, "cases": 2, "report": None, "test_case_sec": 25.0, "passed": 1, "failed": 1, "source": "https://github.com/pytorch/pytorch/blob/abc/test/a.py"}
    assert summary["main_step_idle_sec"] is not None
    trace = json.loads(output.read_text())
    tracks = {event["tid"]: event["args"]["name"] for event in trace["traceEvents"] if event.get("name") == "thread_name"}
    slices = [(tracks[event["tid"]], event["name"], event["ts"], event["dur"]) for event in trace["traceEvents"] if event["ph"] == "X"]
    assert ("job", "queued", 0, 10_000_000) in slices
    assert ("steps", "Test", 20_000_000, 70_000_000) in slices
    assert ("test files 0", "test/a.py", 30_000_000, 20_000_000) in slices
    assert ("test cases 1", "A.test_y", 35_000_000, 15_000_000) in slices
    assert [s[2:] for s in slices if s[0] == "idle in main step"] == [(20_000_000, 10_000_000), (50_000_000, 10_000_000), (70_000_000, 20_000_000)]

    assert next(e for e in trace["traceEvents"] if e.get("name") == "A.test_y")["args"]["source"] == "https://github.com/pytorch/pytorch/blob/abc/test/a.py#L2"


def test_test_line_matches_instantiated_names_to_their_def() -> None:
    source = "\n".join(
        [
            "class TestHelpers:",
            "    def test_add(self): ...",
            "class TestOps(TestCase):",
            "    def test_add(self, device, dtype): ...",
            "    def test_add_inplace(self, device): ...",
        ]
    )
    definitions = find_test_defs(source)

    assert find_test_line(definitions, "TestOpsCUDA", "test_add_cuda_float32") == 4
    assert find_test_line(definitions, "TestOpsCUDA", "test_add_inplace_cuda") == 5
    assert find_test_line(definitions, "TestOpsCUDA", "test_missing") is None


def test_with_links_points_models_and_steps_at_the_job_commit() -> None:
    model = Span("cuda train alexnet", 1_000, 2_000, {"command": "python benchmarks/dynamo/torchbench.py --training"})
    command = Span("python benchmarks/dynamo/torchbench.py --training", 900, 3_000, {"command": "python benchmarks/dynamo/torchbench.py --training"})
    step = Span("Test", 0, 5_000, {"number": 12, "conclusion": "success"})
    timeline = JobTimeline(JOB | {"head_sha": "abc"}, [step], [], LogSpans(commands=[command], models=[model]))

    linked = with_links(timeline, {".ci/docker/ci_commit_pins/torchbench.txt": "pin123\n"}.get)

    assert linked.log.models[0].args["source"] == "https://github.com/pytorch/benchmark/tree/pin123/torchbenchmark/models/alexnet"
    assert linked.log.models[0].args["github_log"] == "https://github.com/x/job/7#step:12:1"
    assert linked.log.commands[0].args["source"] == "https://github.com/pytorch/pytorch/blob/abc/benchmarks/dynamo/torchbench.py"


def test_idle_silences_drop_silence_while_a_test_process_runs() -> None:
    log = LogSpans(
        processes=[Span("inductor/test_flex_attention 2/2", 0, 500_000)],
        silences=[Span("log silent", 10_000, 490_000), Span("log silent", 450_000, 600_000), Span("log silent", 700_000, 760_000)],
    )
    log = LogSpans(processes=log.processes, silences=log.silences, commands=[Span("docker pull image", 690_000, 800_000)])
    timeline = JobTimeline(JOB, [], [], log)

    assert [(s.start_ms, s.end_ms, s.args) for s in idle_silences(timeline)] == [
        (500_000, 600_000, {}),
        (700_000, 760_000, {"during": "docker pull image"}),
    ]


def test_parse_log_keeps_class_filtered_test_processes() -> None:
    log = "\n".join(
        [
            "2026-07-02T13:00:00.0000000Z Running test_ops, not (TestFoo or TestBar) 1/2 ... [2026-07-02 13:00:00][1.0]",
            "2026-07-02T13:01:00.0000000Z Finished test_ops, not (TestFoo or TestBar) 1/2 ... [2026-07-02 13:01:00][2.0], took 1.00min",
        ]
    )

    assert [(s.name, s.duration_ms) for s in parse_log(log).processes] == [("test_ops, not (TestFoo or TestBar) 1/2", 60_000)]


def test_find_test_line_prefers_the_instantiated_class_over_a_longer_def_elsewhere() -> None:
    source = "\n".join(
        [
            "class TestNN(TestCase):",
            "    def test_conv_cuda(self): ...",
            "class TestNNDeviceType(TestCase):",
            "    def test_conv(self, device): ...",
        ]
    )

    assert find_test_line(find_test_defs(source), "TestNNDeviceTypeCUDA", "test_conv_cuda") == 4
    assert find_test_line(find_test_defs(source), "TestNN", "test_conv_cuda") == 2
