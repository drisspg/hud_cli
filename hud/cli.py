from __future__ import annotations

import csv
import io
import json
import socket
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from curl_cffi import requests
from platformdirs import user_cache_dir
from rich.console import Console
from rich.table import Table

from hud import __version__
from hud.auth import CONFIG_PATH, HudConfig, load_config
from hud.gcx import (
    GRAFANA_SERVER,
    GcxError,
    clickhouse_query,
    gcx_failure_message,
    gcx_status,
    install_gcx,
    login_with_hud_token,
    rows_from_gcx_response,
    run_gcx,
)
from hud.logs import (
    LogError,
    download_log,
    extract_patterns,
    extract_test_results,
    filter_sections,
    s3_log_url,
    search_file,
)
from hud.sources import fetch_repo_files
from hud.sql import SqlParamError, parse_params, render_sql, sql_string
from hud.tests_api import (
    TestsApiError,
    openapi_endpoints,
    openapi_parameters,
    parse_query_params,
    tests_api_get,
    tests_api_get_pages,
)
from hud.trace import (
    JobTimeline,
    LogSpans,
    aligned,
    build_trace,
    jobs_sql,
    parse_log,
    source_paths,
    step_spans,
    steps_sql,
    summarize,
    test_spans,
    tests_sql,
    with_links,
)

app = typer.Typer(help="PyTorch CI data CLI backed by Grafana gcx.", no_args_is_help=True)
auth_app = typer.Typer(help="Inspect CLI authentication and setup.", no_args_is_help=True)
gcx_app = typer.Typer(help="Authenticate and query Grafana gcx.", no_args_is_help=True)
log_app = typer.Typer(help="Fetch and search raw CI job logs.", no_args_is_help=True)
tests_app = typer.Typer(
    help="Query HUD's Tests API: per-test health, runs, owners, and HUD page links.",
    no_args_is_help=True,
)
app.add_typer(auth_app, name="auth")
app.add_typer(gcx_app, name="gcx")
app.add_typer(log_app, name="log")
app.add_typer(tests_app, name="tests")
LOG_CACHE_DIR = Path(user_cache_dir("hud-cli", "pytorch")) / "logs"
SYSTEM_DATABASES = ("system", "INFORMATION_SCHEMA", "information_schema")
console = Console()
err_console = Console(stderr=True)


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit()


@app.command()
def job(
    job_id: int = typer.Argument(..., help="GitHub Actions job id."),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    data = {"job_id": job_id, "log_url": s3_log_url(job_id)}
    if output_json:
        print_json(data)
        return
    console.print(data)


def load_timeline(config: HudConfig, job: dict[str, Any], use_log: bool, links: bool, max_tests: int) -> JobTimeline:
    job_id = int(job["id"])
    steps = step_spans(rows_from_gcx_response(clickhouse_query(config, steps_sql(job_id))))
    test_rows = rows_from_gcx_response(
        clickhouse_query(config, tests_sql(job_id, job["started_ms"], job["completed_ms"], max_tests), {"max_execution_time": 120})
    )
    log = LogSpans()
    if use_log and job["runner_name"]:
        try:
            log = parse_log(download_log(job_id, default_log_path(job_id)).read_text(errors="replace"))
        except (LogError, requests.RequestsError) as error:
            err_console.print(f"[yellow]warning:[/yellow] job {job_id}: {error}; trace has no log spans for it")
    timeline = JobTimeline(job, steps, test_spans(test_rows[:max_tests]), log, len(test_rows) > max_tests)
    if links:
        timeline = with_links(timeline, fetch_repo_files(job["head_sha"], source_paths(timeline)).get)
    return timeline


@app.command("trace")
def trace(
    job_ids: Annotated[list[int] | None, typer.Argument(help="GitHub Actions job ids; several share one timeline.")] = None,
    run_id: Annotated[int | None, typer.Option("--run", help="Trace every job of this GitHub Actions workflow run instead.")] = None,
    max_jobs: int = typer.Option(30, "--max-jobs", min=1, max=200, help="Cap on jobs traced from --run, longest first."),
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Trace path. Defaults to ./hud-job-JOB_ID.pftrace, ./hud-run-RUN_ID.pftrace, or ./hud-jobs-FIRST_ID-N.pftrace."),
    ] = None,
    use_log: bool = typer.Option(True, "--log/--no-log", help="Add spans parsed from the raw job logs."),
    links: bool = typer.Option(True, "--links/--no-links", help="Add GitHub source and step-log links; fetches test files at each job's commit."),
    max_tests: int = typer.Option(50_000, "--max-tests", min=1, max=500_000, help="Cap on test-case rows from ci.runs per job."),
    align: bool = typer.Option(False, "--align", help="Shift jobs to start running at the same instant, to compare runs from different times."),
    output_json: bool = typer.Option(False, "--json", help="Print the summary as JSON."),
) -> None:
    """Write a Perfetto .pftrace of where jobs spent their time, nested from steps down to test cases."""
    if bool(job_ids) == (run_id is not None):
        console.print("[red]error:[/red] pass job ids or --run, not both or neither")
        raise typer.Exit(2)
    config = load_config()
    try:
        sql = jobs_sql(run_id=run_id, limit=1000) if run_id is not None else jobs_sql(job_ids, limit=len(job_ids))
        jobs = rows_from_gcx_response(clickhouse_query(config, sql))
        if not jobs:
            raise GcxError(f"no jobs found in default.workflow_job for {f'run {run_id}' if run_id is not None else job_ids}")
        jobs = sorted(jobs, key=lambda job: int(job["created_ms"]) - int(job["completed_ms"]))
        skipped = len(jobs) - max_jobs if run_id is not None else 0
        timelines = [load_timeline(config, job, use_log, links, max_tests) for job in jobs[: max_jobs if run_id is not None else None]]
    except (GcxError, json.JSONDecodeError, KeyError) as error:
        console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    if output:
        path = output
    elif run_id is not None:
        path = Path(f"hud-run-{run_id}.pftrace")
    elif len(job_ids) == 1:
        path = Path(f"hud-job-{job_ids[0]}.pftrace")
    else:
        path = Path(f"hud-jobs-{job_ids[0]}-{len(job_ids)}.pftrace")
    path.write_bytes(build_trace(aligned(timelines) if align else timelines))
    summaries = [summarize(timeline) for timeline in timelines]
    result = {"trace": str(path.resolve()), "open_with": "https://ui.perfetto.dev", "jobs_skipped": max(skipped, 0), "jobs": summaries}
    if output_json:
        print_json(result)
        return
    for summary, timeline in zip(summaries, timelines, strict=True):
        console.print(f"[bold]{summary['name']}[/bold] ({summary['conclusion']})  {summary['html_url']}")
        idle = summary["main_step_idle_sec"]
        console.print(
            f"  queue {summary['queue_sec']}s, runtime {summary['runtime_sec']}s"
            + (f", {summary['main_step']} step {summary['main_step_sec']}s" if summary["main_step"] else "")
            + (f" ({idle}s with no test case running)" if idle is not None else "")
            + f", {summary['log_silent_outside_tests_sec']}s log-silent outside tests"
        )
        console.print("  " + ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in summary["counts"].items()))
        if timeline.truncated:
            console.print(f"  [yellow]test cases truncated at --max-tests {max_tests}[/yellow]")
    if skipped > 0:
        console.print(f"[yellow]skipped {skipped} shorter jobs past --max-jobs {max_jobs}[/yellow]")
    console.print(f"Wrote {result['trace']}; open it in {result['open_with']} (drag and drop).")


@log_app.command("url")
def log_url(job_id: int = typer.Argument(..., help="GitHub Actions job id.")) -> None:
    console.print(s3_log_url(job_id))


@log_app.command("download")
def log_download(
    job_id: int = typer.Argument(..., help="GitHub Actions job id."),
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Path to write the raw log. Defaults to the hud-cli cache."),
    ] = None,
) -> None:
    console.print(str(download_log(job_id, output or default_log_path(job_id))))


@log_app.command("search")
def log_search(
    pattern: str = typer.Argument(..., help="Regex pattern to search for."),
    path: Annotated[Path | None, typer.Option("--path", help="Existing local log file.")] = None,
    job_id: Annotated[int | None, typer.Option("--job-id", help="Download and search this job log.")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Path for downloaded job log.")] = None,
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    output_json: bool = typer.Option(False, "--json", help="Print machine-readable matches."),
) -> None:
    log_path = resolve_log_path(path, job_id, output)
    matches = search_file(log_path, pattern, limit)
    data = {
        "path": str(log_path),
        "pattern": pattern,
        "matches": [match.__dict__ for match in matches],
    }
    if output_json:
        print_json(data)
        return
    for match in matches:
        console.print(f"{match.line_number}: {match.text}")


@log_app.command("patterns")
def log_patterns(
    path: Annotated[Path, typer.Argument(help="Existing local log file.")],
    output_json: bool = typer.Option(True, "--json/--table"),
) -> None:
    data = extract_patterns(path)
    if output_json:
        print_json(data)
        return
    table = Table(title="Log pattern counts")
    table.add_column("Pattern")
    table.add_column("Count")
    for name, count in data["counts"].items():
        table.add_row(name, str(count))
    console.print(table)


@log_app.command("tests")
def log_tests(
    path: Annotated[Path, typer.Argument(help="Existing local log file.")],
    output_json: bool = typer.Option(True, "--json/--table"),
) -> None:
    data = extract_test_results(path)
    if output_json:
        print_json(data)
        return
    console.print(data)


@log_app.command("sections")
def log_sections(
    path: Annotated[Path, typer.Argument(help="Existing local log file.")],
    start_pattern: Annotated[str, typer.Option("--start", help="Regex that starts a section.")] = ...,
    end_pattern: Annotated[str | None, typer.Option("--end", help="Regex that ends a section.")] = None,
    max_lines: int = typer.Option(100, "--max-lines", min=1, max=1000),
    output_json: bool = typer.Option(True, "--json/--text"),
) -> None:
    sections = filter_sections(path, start_pattern, end_pattern, max_lines)
    data = {
        "path": str(path),
        "section_count": len(sections),
        "sections": [section.__dict__ for section in sections],
    }
    if output_json:
        print_json(data)
        return
    for section in sections:
        console.print(section.content)


@app.command("doctor")
def doctor() -> None:
    render_auth_doctor()


@auth_app.command("doctor")
def auth_doctor() -> None:
    render_auth_doctor()


@auth_app.command("setup")
def auth_setup() -> None:
    table = Table(title="Authentication setup")
    table.add_column("Access")
    table.add_column("How to get it")
    table.add_row(
        "GitHub",
        "Run `gh auth login --hostname github.com --git-protocol ssh --web`.",
    )
    table.add_row(
        "Grafana/gcx",
        "Run `hud gcx install` if gcx is missing, then `hud gcx login`. Login mints a short-lived Grafana token from HUD using your GitHub auth and runs `gcx login --yes pytorchci --server https://pytorchci.grafana.net` with the token in GRAFANA_TOKEN, never printing it.",
    )
    table.add_row(
        "ClickHouse",
        "Use `hud gcx chq SQL` after `hud gcx login`. Queries go through Grafana's ClickHouse datasource, not raw ClickHouse credentials.",
    )
    table.add_row(
        "Tests API",
        "Use `hud tests api /api/tests -q repo=pytorch/pytorch`. Requests use your GitHub auth against HUD's /api/authed/ routes.",
    )
    console.print(table)


def render_auth_doctor() -> None:
    config = load_config()
    status = gcx_status(config)
    table = Table(title="hud auth")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("base_url", config.base_url)
    table.add_row("config", str(CONFIG_PATH))
    table.add_row("GITHUB_TOKEN/gh auth", "set" if config.github_token else "not set")
    table.add_row("GRAFANA_TOKEN", "set" if status.grafana_token_set else "not set")
    table.add_row("gcx", status.label)
    table.add_row("primary", "Grafana gcx via HUD-minted token")
    console.print(table)


@gcx_app.command("doctor")
def gcx_doctor(
    output_json: bool = typer.Option(False, "--json", help="Print machine-readable status."),
) -> None:
    status = gcx_status(load_config())
    data = {
        "available": status.available,
        "path": status.path,
        "source": status.source,
        "grafana_token_set": status.grafana_token_set,
    }
    if output_json:
        print_json(data)
        return
    table = Table(title="gcx")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("available", "yes" if status.available else "no")
    table.add_row("path", status.path or "not found")
    table.add_row("source", status.source)
    table.add_row("GRAFANA_TOKEN", "set" if status.grafana_token_set else "not set")
    console.print(table)


@gcx_app.command("install")
def gcx_install(
    force: bool = typer.Option(False, "--force", help="Reinstall even if the managed gcx binary already exists."),
) -> None:
    try:
        console.print(str(install_gcx(force=force)))
    except Exception as error:
        console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error


@gcx_app.command("login")
def gcx_login(
    token_name: str | None = typer.Option(None, "--token-name", help="Name for the HUD-minted Grafana token."),
) -> None:
    config = load_config()
    try:
        result = login_with_hud_token(config, token_name or hostname_token_name())
    except GcxError as error:
        console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    if result.returncode != 0:
        console.print(f"[red]error:[/red] {gcx_failure_message(result, 'gcx login failed')}")
        raise typer.Exit(result.returncode)
    console.print(f"Logged gcx context pytorchci into {GRAFANA_SERVER}.")
    try:
        clickhouse_query(config, "SELECT 1")
    except (GcxError, json.JSONDecodeError, KeyError) as error:
        console.print(f"[red]error:[/red] gcx login completed, but HUD ClickHouse verification failed: {error}")
        raise typer.Exit(1) from error
    console.print("[green]Verified HUD ClickHouse access with SELECT 1.[/green]")


@gcx_app.command("tables")
def gcx_tables(
    database: str | None = typer.Option(None, "--database", "-d", help="Limit to one ClickHouse database; default lists all."),
    limit: int = typer.Option(500, "--limit", min=1, max=5000),
    output_json: bool = typer.Option(False, "--json", help="Print rows as JSON."),
) -> None:
    filters = [database_filter(database), "name NOT LIKE '.%'"]
    sql = (
        "SELECT database, name, engine, total_rows, comment "
        "FROM system.tables "
        f"WHERE {' AND '.join(filters)} "
        "ORDER BY database, name "
        f"LIMIT {limit}"
    )
    render_clickhouse_sql(sql, output_json)


@gcx_app.command("describe")
def gcx_describe(
    table: str = typer.Argument(..., help="Table to describe, optionally database.table."),
    database: str = typer.Option("default", "--database", "-d", help="Default database when TABLE is unqualified."),
    output_json: bool = typer.Option(False, "--json", help="Print rows as JSON."),
) -> None:
    render_clickhouse_sql(f"DESCRIBE TABLE {qualified_table_name(table, database)}", output_json)


@gcx_app.command("columns")
def gcx_columns(
    pattern: str | None = typer.Argument(None, help="Case-insensitive table/column/comment search text."),
    database: str | None = typer.Option(None, "--database", "-d", help="Limit to one ClickHouse database; default searches all."),
    table: str | None = typer.Option(None, "--table", "-t", help="Limit search to one table."),
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
    output_json: bool = typer.Option(False, "--json", help="Print rows as JSON."),
) -> None:
    filters = [database_filter(database), "table NOT LIKE '.%'"]
    if table:
        filters.append(f"table = {sql_string(table)}")
    if pattern:
        like = sql_string(f"%{pattern}%")
        filters.append(f"(table ILIKE {like} OR name ILIKE {like} OR comment ILIKE {like})")
    sql = (
        "SELECT database, table, name, type, comment "
        "FROM system.columns "
        f"WHERE {' AND '.join(filters)} "
        "ORDER BY database, table, position "
        f"LIMIT {limit}"
    )
    render_clickhouse_sql(sql, output_json)


@gcx_app.command("sample")
def gcx_sample(
    table: str = typer.Argument(..., help="Table to sample, optionally database.table."),
    database: str = typer.Option("default", "--database", "-d", help="Default database when TABLE is unqualified."),
    limit: int = typer.Option(10, "--limit", min=1, max=100),
    output_json: bool = typer.Option(True, "--json/--table", help="Print rows as JSON."),
) -> None:
    render_clickhouse_sql(f"SELECT * FROM {qualified_table_name(table, database)} LIMIT {limit}", output_json)


@gcx_app.command("chq")
def gcx_chq(
    sql: str | None = typer.Argument(None, help="ClickHouse SQL to run through Grafana's PyTorch datasource. Use '-' to read stdin."),
    sql_file: Annotated[Path | None, typer.Option("--file", "-f", help="Path to a .sql file to run.")] = None,
    param: Annotated[
        list[str] | None,
        typer.Option("--param", "-p", help="Fill a {name:Type} placeholder as name=value; repeatable."),
    ] = None,
    max_seconds: int = typer.Option(60, "--max-seconds", min=1, max=300, help="ClickHouse max_execution_time for SELECT/WITH queries."),
    output_json: bool = typer.Option(False, "--json", help="Print parsed rows as JSON."),
    output_csv: bool = typer.Option(False, "--csv", help="Print parsed rows as CSV."),
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write rows to this file (CSV with --csv, else JSON).")] = None,
    raw: bool = typer.Option(False, "--raw", help="Print raw Grafana datasource JSON."),
) -> None:
    if output_json and output_csv:
        raise typer.BadParameter("use --json or --csv, not both")
    try:
        query = render_sql(read_sql(sql, sql_file), parse_params(param or []))
        data = clickhouse_query(load_config(), query, {"max_execution_time": max_seconds})
    except (GcxError, SqlParamError, json.JSONDecodeError, KeyError) as error:
        console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    if raw and output:
        output.write_text(json.dumps(data, indent=2, default=str))
        console.print(f"Wrote raw response to {output}")
        return
    if raw:
        print_json(data)
        return
    rows = rows_from_gcx_response(data)
    if output:
        output.write_text(rows_to_csv(rows) if output_csv else json.dumps(rows, indent=2, default=str))
        console.print(f"Wrote {len(rows)} rows to {output}")
        return
    if output_csv:
        sys.stdout.write(rows_to_csv(rows))
        return
    if output_json:
        print_json(rows)
        return
    render_rows(rows)


@gcx_app.command(
    "run",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def gcx_run(ctx: typer.Context) -> None:
    args = list(ctx.args)
    if args[:1] == ["--"]:
        args = args[1:]
    try:
        result = run_gcx(load_config(), args)
    except GcxError as error:
        console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    if result.stdout:
        console.print(result.stdout, end="")
    if result.stderr:
        err_console.print(result.stderr, end="")
    raise typer.Exit(result.returncode)


@tests_app.command("api")
def tests_api(
    path: str = typer.Argument(..., help="OpenAPI path, e.g. /api/tests or /api/tests/test/TEST_ID."),
    query: Annotated[
        list[str] | None,
        typer.Option("--query", "-q", help="Query parameter as key=value; repeat for multiple or repeated keys."),
    ] = None,
    all_pages: bool = typer.Option(False, "--all", help="Follow pagination cursors and concatenate list fields."),
    max_pages: int = typer.Option(10, "--max-pages", min=1, max=100, help="Page cap for --all."),
) -> None:
    """GET a Tests API endpoint and print its JSON response."""
    try:
        config, params = load_config(), parse_query_params(query or [])
        data = tests_api_get_pages(config, path, params, max_pages) if all_pages else tests_api_get(config, path, params)
    except TestsApiError as error:
        console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    print_json(data)


@tests_app.command("schema")
def tests_schema(
    path: str | None = typer.Argument(None, help="Show parameters for this OpenAPI path; default lists endpoints."),
    output_json: bool = typer.Option(False, "--json", help="Print rows as JSON."),
) -> None:
    """Summarize HUD's Tests API OpenAPI schema."""
    try:
        schema = tests_api_get(load_config(), "/api/openapi.json")
        rows = openapi_parameters(schema, path) if path else openapi_endpoints(schema)
    except TestsApiError as error:
        console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    if output_json:
        print_json(rows)
        return
    render_rows(rows)


def default_log_path(job_id: int) -> Path:
    return LOG_CACHE_DIR / f"hud-job-{job_id}.log"


def database_filter(database: str | None) -> str:
    if database:
        return f"database = {sql_string(database)}"
    return f"database NOT IN ({', '.join(sql_string(name) for name in SYSTEM_DATABASES)})"


def render_clickhouse_sql(sql: str, output_json: bool) -> None:
    try:
        rows = rows_from_gcx_response(clickhouse_query(load_config(), sql))
    except (GcxError, json.JSONDecodeError, KeyError) as error:
        console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    if output_json:
        print_json(rows)
        return
    render_rows(rows)


def qualified_table_name(table: str, database: str) -> str:
    parts = table.split(".", 1)
    if len(parts) == 2:
        return f"{quote_identifier(parts[0])}.{quote_identifier(parts[1])}"
    return f"{quote_identifier(database)}.{quote_identifier(table)}"


def quote_identifier(value: str) -> str:
    if not value:
        raise typer.BadParameter("identifier cannot be empty")
    return f"`{value.replace('`', '``')}`"


def read_sql(sql: str | None, sql_file: Path | None) -> str:
    if sql and sql_file:
        raise typer.BadParameter("use SQL argument or --file, not both")
    if sql_file:
        return sql_file.read_text()
    if sql == "-":
        return sys.stdin.read()
    if sql:
        return sql
    raise typer.BadParameter("provide SQL, '-' for stdin, or --file")


def hostname_token_name() -> str:
    return socket.gethostname() or "hud-cli"


def resolve_log_path(path: Path | None, job_id: int | None, output: Path | None) -> Path:
    if path and job_id:
        raise typer.BadParameter("use either --path or --job-id, not both")
    if path:
        return path
    if job_id is None:
        raise typer.BadParameter("provide --path or --job-id")
    return download_log(job_id, output or default_log_path(job_id))


def print_json(data: Any) -> None:
    console.print_json(json.dumps(data, indent=2, sort_keys=True, default=str))


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value, (list, dict)) else value for key, value in row.items()})
    return buffer.getvalue()


def render_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        console.print("No rows returned.")
        return
    table = Table(title="Query results")
    columns = list(rows[0].keys())
    for column in columns:
        table.add_column(str(column))
    for row in rows:
        table.add_row(*(str(row.get(column, "")) for column in columns))
    console.print(table)


def run() -> int:
    try:
        app()
        return 0
    except (GcxError, LogError, TestsApiError) as error:
        console.print(f"[red]error:[/red] {error}")
        return 1


if __name__ == "__main__":
    run()
