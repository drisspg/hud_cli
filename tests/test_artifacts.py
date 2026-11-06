import io
import zipfile

from hud.artifacts import report_rows

SERIAL = """<testsuites><testsuite name="pytest" time="3.1" timestamp="2026-11-06T18:00:00.000000">
<testcase classname="TestFP8" name="test_a" time="1.0" file="inductor/test_fp8.py" />
<testcase classname="TestFP8" name="test_b" time="2.0" file="inductor/test_fp8.py"><skipped /></testcase>
</testsuite></testsuites>"""
XDIST = """<testsuites><testsuite name="pytest" time="2.0" timestamp="2026-11-06T18:00:00.000000">
<testcase classname="T" name="test_1" time="2.0" file="test_x.py" />
<testcase classname="T" name="test_2" time="2.0" file="test_x.py"><failure /></testcase>
</testsuite></testsuites>"""
T0 = 1_793_988_000_000  # 2026-11-06T18:00:00Z


def archive(**reports: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for name, xml in reports.items():
            zipped.writestr(f"test-reports/python-pytest/{name}/{name}-1.xml", xml)
        zipped.writestr("test-reports/summary.xml", "<not-a-report/>")
    return buffer.getvalue()


def test_serial_suite_cases_run_back_to_back_from_the_utc_suite_start() -> None:
    rows = report_rows(archive(fp8=SERIAL))

    assert [(r["file"], r["case_name"], r["outcome"], r["start_ms"] - T0, r["end_ms"] - T0) for r in rows] == [
        ("test/inductor/test_fp8.py", "test_a", "passed", 0, 1_000),
        ("test/inductor/test_fp8.py", "test_b", "skipped", 1_000, 3_000),
    ]
    assert {r["report"] for r in rows} == {"fp8-1"}


def test_xdist_suite_cases_are_packed_onto_parallel_workers() -> None:
    rows = report_rows(archive(x=XDIST))

    assert [(r["case_name"], r["outcome"], r["start_ms"] - T0, r["end_ms"] - T0) for r in rows] == [
        ("test_1", "passed", 0, 2_000),
        ("test_2", "failed", 0, 2_000),
    ]
