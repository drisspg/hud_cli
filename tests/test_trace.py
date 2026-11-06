import json
import struct

from typer.testing import CliRunner

import hud.cli as cli
from hud.cli import app
from hud.trace import (
    JobTimeline,
    LogSpans,
    Span,
    aligned,
    find_test_defs,
    find_test_line,
    idle_silences,
    nest_lanes,
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
    "id": 7, "run_id": 70, "name": "linux / test (default, 1, 2, gpu)", "workflow_name": "trunk",
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


def protobuf_fields(data: bytes):
    """(field number, value) pairs of one protobuf message; length-delimited values stay bytes."""
    index = 0

    def varint() -> int:
        nonlocal index
        value = shift = 0
        while True:
            byte = data[index]
            index += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if byte < 0x80:
                return value

    while index < len(data):
        key = varint()
        wire = key & 7
        if wire == 0:
            value = varint()
        elif wire == 1:
            value = struct.unpack("<d", data[index:index + 8])[0]
            index += 8
        else:
            size = varint()
            value = data[index:index + size]
            index += size
        yield key >> 3, value


def read_slices(trace: bytes) -> list[dict]:
    """Pair TrackEvent begin/end packets into slices with their track name and stack depth."""
    tracks, stacks, slices = {}, {}, []
    for _, packet in protobuf_fields(trace):
        packet = dict(protobuf_fields(packet))
        if 60 in packet:
            descriptor = dict(protobuf_fields(packet[60]))
            tracks[descriptor[1]] = descriptor[2].decode()
            continue
        event = list(protobuf_fields(packet[11]))
        fields = dict(event)
        stack = stacks.setdefault(fields[11], [])
        if fields[9] == 1:
            args = {}
            for number, value in event:
                if number == 4:
                    annotation = dict(protobuf_fields(value))
                    name = annotation.pop(10).decode()
                    args[name] = next(v.decode() if isinstance(v, bytes) else v for v in annotation.values())
            stack.append({"track": tracks[fields[11]], "name": fields[23].decode(), "cat": fields[22].decode(), "depth": len(stack), "start_ms": packet[8] // 1_000_000, "args": args})
        else:
            item = stack.pop()
            slices.append(item | {"dur_ms": packet[8] // 1_000_000 - item["start_ms"]})
    assert not any(stacks.values())
    return slices


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
    assert [(s.name, s.duration_ms, s.args["xtrace_depth"]) for s in spans.commands] == [
        ("pip install torch.whl", 10_000, 1),
        ("echo quick", 100, 2),
        ("python benchmarks/dynamo/torchbench.py --training", 54_900, 1),
    ]
    assert [(s.name, s.duration_ms, s.args) for s in spans.models] == [
        ("cuda train alexnet", 46_000, {"command": "python benchmarks/dynamo/torchbench.py --training", "result": "pass"}),
        ("cuda eval sam", 8_000, {"command": "python benchmarks/dynamo/torchbench.py --training", "result": "1.379x"}),
    ]
    assert [(s.start_ms % 3_600_000, s.duration_ms) for s in spans.silences] == [(20_000, 38_000)]


def test_nest_lanes_stacks_contained_spans_and_spills_partial_overlaps() -> None:
    spans = [("step", Span("step", 0, 100_000)), ("file", Span("a", 0, 100_000)), ("test", Span("x", 10_000, 20_000, {"file": "a"})), ("test", Span("y", 19_500, 20_800, {"file": "a"})), ("test", Span("z", 30_000, 40_000, {"file": "a"}))]

    lanes = [[(ms, span.name if span else "end") for ms, _, span in events] for events in nest_lanes(spans)]

    assert lanes == [
        [(0, "step"), (0, "a"), (10_000, "x"), (20_000, "end"), (30_000, "z"), (40_000, "end"), (100_000, "end"), (100_000, "end")],
        [(19_500, "y"), (20_800, "end")],
    ]


def test_nest_lanes_clips_children_that_overrun_a_whole_second_step_end() -> None:
    spans = [("step", Span("Test", 0, 5_000)), ("action", Span("Run test.sh", 100, 5_400)), ("action", Span("Run upload", 100, 7_000))]

    lanes = [[(ms, span.name if span else "end") for ms, _, span in events] for events in nest_lanes(spans)]

    assert lanes == [[(0, "Test"), (100, "Run test.sh"), (5_000, "end"), (5_000, "end")], [(100, "Run upload"), (7_000, "end")]]


def test_uncovered_reports_gaps_between_overlapping_spans() -> None:
    gaps = uncovered(Span("Test", 0, 10_000), [Span("a", 2_000, 4_000), Span("b", 3_000, 5_000), Span("c", 5_500, 9_500)])

    assert [(gap.start_ms, gap.end_ms) for gap in gaps] == [(0, 2_000)]


def test_trace_writes_perfetto_events_and_summary(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "clickhouse_query", fake_query)
    monkeypatch.setattr(cli, "fetch_repo_files", lambda sha, paths: {"test/a.py": "class A:\n    def test_y(self):\n        pass\n"})
    output = tmp_path / "job.pftrace"

    result = runner.invoke(app, ["trace", "7", "--no-log", "-o", str(output), "--json"])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["jobs"][0]
    assert summary["queue_sec"] == 10
    assert summary["main_step_sec"] == 70
    assert summary["main_step_idle_sec"] == 40
    assert summary["slowest_files"][0] | {"report": None} == {"name": "test/a.py", "sec": 20.0, "cases": 2, "report": None, "test_case_sec": 25.0, "passed": 1, "failed": 1, "source": "https://github.com/pytorch/pytorch/blob/abc/test/a.py"}
    assert summary["main_step_idle_sec"] is not None
    slices = read_slices(output.read_bytes())
    stacks = {(s["track"], s["depth"], s["cat"], s["name"], s["start_ms"], s["dur_ms"]) for s in slices}
    assert ("timeline 0", 0, "job", "queued", 0, 10_000) in stacks
    assert ("timeline 0", 1, "step", "Test", 20_000, 70_000) in stacks
    assert ("timeline 0", 2, "file", "test/a.py", 30_000, 20_000) in stacks
    assert ("timeline 0", 3, "test", "A.test_x", 30_000, 10_000) in stacks
    assert ("timeline 1", 0, "test", "A.test_y", 35_000, 15_000) in stacks
    assert [(s["start_ms"], s["dur_ms"]) for s in slices if s["track"] == "idle in main step"] == [(20_000, 10_000), (50_000, 10_000), (70_000, 20_000)]
    assert next(s for s in slices if s["name"] == "A.test_y")["args"]["source"] == "https://github.com/pytorch/pytorch/blob/abc/test/a.py#L2"
    assert next(s for s in slices if s["name"] == "running")["args"]["truncated_tests"] == 0


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


def test_trace_puts_several_jobs_on_one_clock_longest_first(monkeypatch, tmp_path) -> None:
    never_ran = JOB | {"id": 8, "name": "mac / test (mps)", "runner_name": "", "conclusion": "cancelled", "created_ms": 0, "started_ms": 0, "completed_ms": 600_000}

    def query(config, sql, settings=None):
        if "ARRAY JOIN steps" in sql:
            return frame(STEPS if "id = 7" in sql else [])
        if "ci.runs" in sql:
            return frame(TESTS if "= 7\n" in sql else [])
        return frame([JOB, never_ran])

    monkeypatch.setattr(cli, "clickhouse_query", query)
    output = tmp_path / "jobs.pftrace"

    result = runner.invoke(app, ["trace", "7", "8", "--no-log", "--no-links", "-o", str(output), "--json"])

    assert result.exit_code == 0, result.output
    jobs = {job["job_id"]: job for job in json.loads(result.output)["jobs"]}
    assert (jobs[8]["queue_sec"], jobs[8]["runtime_sec"]) == (600, 0)
    slices = read_slices(output.read_bytes())
    assert ("queued (never picked up)", 0, 600_000) in {(s["name"], s["start_ms"], s["dur_ms"]) for s in slices}
    assert ("Test", 20_000, 70_000) in {(s["name"], s["start_ms"], s["dur_ms"]) for s in slices}


def test_aligned_jobs_start_running_together_and_keep_their_queues() -> None:
    early = JobTimeline(JOB, [Span("Test", 20_000, 90_000)], [], LogSpans(processes=[Span("test_fp8 1/1", 30_000, 40_000)]))
    late = JobTimeline(JOB | {"created_ms": 1_000_000, "started_ms": 1_030_000, "completed_ms": 1_100_000}, [], [], LogSpans())

    first, second = aligned([early, late])

    assert [(t.job["created_ms"], t.job["started_ms"]) for t in (first, second)] == [(20_000, 30_000), (0, 30_000)]
    assert (first.steps[0].start_ms, first.log.processes[0].start_ms) == (40_000, 50_000)


def test_trace_fails_when_a_requested_job_is_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "clickhouse_query", fake_query)

    result = runner.invoke(app, ["trace", "7", "999", "--no-log", "--no-links", "-o", str(tmp_path / "t.pftrace")])

    assert result.exit_code == 1
    assert "[999]" in result.output
    assert not (tmp_path / "t.pftrace").exists()


def test_nest_lanes_never_nests_a_span_under_one_of_the_same_or_finer_category() -> None:
    spans = [("file", Span("test_a.py", 0, 100_000)), ("test", Span("A.test_x", 0, 90_000, {"file": "test_a.py"})), ("file", Span("test_b.py", 10_000, 20_000))]

    lanes = [[span.name for _, _, span in events if span] for events in nest_lanes(spans)]

    assert lanes == [["test_a.py", "A.test_x"], ["test_b.py"]]


def test_trace_falls_back_to_the_test_reports_artifact_when_ci_runs_is_empty(monkeypatch, tmp_path) -> None:
    def query(config, sql, settings=None):
        return frame([]) if "ci.runs" in sql else fake_query(config, sql, settings)

    archives = []
    monkeypatch.setattr(cli, "clickhouse_query", query)
    monkeypatch.setattr(cli, "fetch_test_reports", lambda run_id, job_id: archives.append((run_id, job_id)) or b"zip")
    monkeypatch.setattr(cli, "report_rows", lambda archive: TESTS)

    result = runner.invoke(app, ["trace", "7", "--no-log", "--no-links", "-o", str(tmp_path / "t.pftrace"), "--json"])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)["jobs"][0]
    assert archives == [(70, 7)]
    assert (summary["test_cases_source"], summary["counts"]["test_cases"]) == ("test-reports artifact", 3)


def test_nest_lanes_puts_files_and_cases_under_their_own_concurrent_process() -> None:
    spans = [
        ("process", Span("inductor/test_a 1/1", 0, 100_000)),
        ("process", Span("test_b 1/1", 1_000, 50_000)),
        ("file", Span("test/inductor/test_a.py", 2_000, 90_000)),
        ("file", Span("test/test_b.py", 3_000, 40_000)),
        ("test", Span("B.test_y", 4_000, 5_000, {"file": "test/test_b.py"})),
        ("test", Span("A.test_x", 6_000, 7_000, {"file": "test/inductor/test_a.py"})),
    ]

    lanes = [[span.name for _, _, span in events if span] for events in nest_lanes(spans)]

    assert lanes == [["inductor/test_a 1/1", "test/inductor/test_a.py", "A.test_x"], ["test_b 1/1", "test/test_b.py", "B.test_y"]]
