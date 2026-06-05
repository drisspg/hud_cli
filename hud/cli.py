from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from hud import __version__
from hud.auth import CONFIG_PATH, load_config
from hud.client import HudClient, HudError
from hud.gcx import GcxError, gcx_status, run_gcx
from hud.logs import (
    LogError,
    download_log,
    extract_patterns,
    extract_test_results,
    filter_sections,
    s3_log_url,
    search_file,
)
from hud.models import filtered_commits
from hud.queries import (
    QUERY_CATALOG,
    QuerySourceError,
    fetch_query_source,
    window_params,
)

app = typer.Typer(help="PyTorch HUD and ClickHouse CI data CLI.", no_args_is_help=True)
query_app = typer.Typer(help="Run named HUD ClickHouse queries.", no_args_is_help=True)
recipe_app = typer.Typer(help="Run curated PyTorch CI data recipes.", no_args_is_help=True)
auth_app = typer.Typer(help="Inspect HUD authentication and configuration.", no_args_is_help=True)
gcx_app = typer.Typer(help="Inspect and invoke Grafana gcx.", no_args_is_help=True)
log_app = typer.Typer(help="Fetch and search raw CI job logs.", no_args_is_help=True)
search_app = typer.Typer(help="Search historical CI failures.", no_args_is_help=True)
app.add_typer(query_app, name="query")
app.add_typer(recipe_app, name="recipe")
app.add_typer(auth_app, name="auth")
app.add_typer(gcx_app, name="gcx")
app.add_typer(log_app, name="log")
app.add_typer(search_app, name="search")
console = Console()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", help="Show version and exit."),
) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit()


@app.command()
def status(
    branch_or_sha: str = typer.Argument("main", help="Branch name or commit SHA."),
    repo: str = typer.Option("pytorch/pytorch", "--repo", "-r"),
    page: int = typer.Option(1, min=1),
    per_page: int = typer.Option(10, min=1, max=100),
    failures: bool = typer.Option(False, "--failures", help="Show failing jobs."),
    success: bool = typer.Option(False, "--success", help="Include successful jobs."),
    pending: bool = typer.Option(False, "--pending", help="Include queued/pending jobs."),
    job_regex: str | None = typer.Option(None, "--job-regex", help="Filter selected jobs by name."),
    failure_regex: str | None = typer.Option(None, "--failure-regex", help="Filter selected jobs by failure text."),
    compact_json: bool = typer.Option(False, "--compact-json", help="Print normalized bounded JSON."),
    output_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    owner, name = parse_repo(repo)
    data = new_client().hud_data(owner, name, branch_or_sha, page, per_page)
    if output_json:
        print_json(data)
        return
    commits = filtered_commits(data, success, pending, failures, job_regex, failure_regex)
    if compact_json:
        print_json({"commits": commits})
        return
    render_commits(commits, failures or success or pending or bool(job_regex) or bool(failure_regex))


@app.command()
def job(
    job_id: int = typer.Argument(..., help="HUD job id."),
    artifacts: bool = typer.Option(False, "--artifacts"),
    log_url: bool = typer.Option(True, "--log-url/--no-log-url"),
    output_json: bool = typer.Option(False, "--json"),
) -> None:
    data: dict[str, Any] = {"job_id": job_id}
    if log_url:
        data["log_url"] = s3_log_url(job_id)
    if artifacts:
        data["artifacts"] = new_client().artifacts("s3", job_id)
    if output_json:
        print_json(data)
        return
    console.print(data)


@query_app.command("run")
def query_run(
    name: str = typer.Argument(..., help="HUD ClickHouse query name."),
    param: Annotated[
        list[str] | None,
        typer.Option("--param", "-p", help="Query parameter as key=value. Repeatable."),
    ] = None,
    output_json: bool = typer.Option(True, "--json/--table"),
) -> None:
    render_query_result(
        new_client().clickhouse_query(name, parse_params(param or [])), output_json
    )


@query_app.command("list")
def query_list() -> None:
    table = Table(title="HUD ClickHouse query catalog")
    table.add_column("Name")
    table.add_column("Description")
    for query in QUERY_CATALOG.values():
        table.add_row(query.name, query.description)
    console.print(table)


@query_app.command("explain")
def query_explain(
    name: str = typer.Argument(..., help="Query name from `hud query list`."),
    output_json: bool = typer.Option(False, "--json", help="Print machine-readable metadata."),
) -> None:
    query = QUERY_CATALOG.get(name)
    if query is None:
        raise typer.BadParameter(f"unknown query: {name}")
    data = {
        "name": query.name,
        "description": query.description,
        "example_params": query.example_params,
    }
    if output_json:
        print_json(data)
        return
    console.print(data)


@query_app.command("source")
def query_source(
    name: str = typer.Argument(..., help="Query name under pytorch/test-infra."),
    sql: bool = typer.Option(True, "--sql/--no-sql", help="Fetch query.sql."),
    params: bool = typer.Option(True, "--params/--no-params", help="Fetch params.json."),
    output_json: bool = typer.Option(True, "--json/--text"),
) -> None:
    data: dict[str, Any] = {"name": name}
    if sql:
        data["query_sql"] = fetch_query_source(name, "query.sql")
    if params:
        data["params_json"] = fetch_query_source(name, "params.json")
    if output_json:
        print_json(data)
        return
    if "query_sql" in data:
        console.print(data["query_sql"])
    if "params_json" in data:
        console.print(data["params_json"])


@query_app.command("examples")
def query_examples() -> None:
    examples = [
        "hud query run queued_jobs",
        "hud query run master_commit_red -p startTime=2026-06-01T00:00:00 -p stopTime=2026-06-05T00:00:00 -p timezone=America/Los_Angeles -p granularity=day -p usePercentage=true",
        "hud query run flaky_tests/across_jobs -p startTime=2026-06-01T00:00:00 -p stopTime=2026-06-05T00:00:00",
    ]
    for example in examples:
        console.print(example)


@recipe_app.command("queued")
def recipe_queued(
    output_json: bool = typer.Option(True, "--json/--table"),
) -> None:
    render_query_result(new_client().clickhouse_query("queued_jobs", {}), output_json)


@recipe_app.command("trunk-red")
def recipe_trunk_red(
    days: int = typer.Option(7, "--days", min=1, max=90),
    timezone: str = typer.Option("America/Los_Angeles", "--timezone"),
    granularity: str = typer.Option("day", "--granularity"),
    output_json: bool = typer.Option(True, "--json/--table"),
) -> None:
    params: dict[str, Any] = {
        **window_params(days),
        "timezone": timezone,
        "granularity": granularity,
        "usePercentage": True,
    }
    render_query_result(new_client().clickhouse_query("master_commit_red", params), output_json)


@recipe_app.command("flaky-test")
def recipe_flaky_test(
    test_name: str | None = typer.Argument(None, help="Optional test name filter."),
    days: int = typer.Option(7, "--days", min=1, max=90),
    output_json: bool = typer.Option(True, "--json/--table"),
) -> None:
    params: dict[str, Any] = window_params(days)
    if test_name:
        params["test_name"] = test_name
    render_query_result(new_client().clickhouse_query("flaky_tests/across_jobs", params), output_json)


@recipe_app.command("tts")
def recipe_tts(
    days: int = typer.Option(7, "--days", min=1, max=90),
    percentile: bool = typer.Option(False, "--percentile", help="Use percentile query instead of average."),
    output_json: bool = typer.Option(True, "--json/--table"),
) -> None:
    query_name = "tts_percentile" if percentile else "tts_avg"
    render_query_result(new_client().clickhouse_query(query_name, window_params(days)), output_json)


@recipe_app.command("slow-test-files")
def recipe_slow_test_files(
    periodic: bool = typer.Option(False, "--periodic", help="Use successful periodic jobs instead of recent strict commits."),
    limit: int = typer.Option(20, "--limit", min=1, max=500),
    output_json: bool = typer.Option(True, "--json/--table"),
) -> None:
    query_name = "test_time_per_file_periodic_jobs" if periodic else "test_time_per_file"
    rows = new_client().clickhouse_query(query_name, {})
    if not isinstance(rows, list):
        raise typer.BadParameter(f"{query_name} returned {type(rows).__name__}; expected a list")
    sorted_rows = sorted(rows, key=lambda row: float(row.get("time") or 0), reverse=True)[:limit]
    render_query_result(sorted_rows, output_json)


@recipe_app.command("disabled-tests")
def recipe_disabled_tests(
    days: int = typer.Option(7, "--days", min=1, max=90),
    label: str = typer.Option("skipped", "--label"),
    repo: str = typer.Option("pytorch/pytorch", "--repo"),
    state: str = typer.Option("open", "--state"),
    granularity: str = typer.Option("day", "--granularity"),
    output_json: bool = typer.Option(True, "--json/--table"),
) -> None:
    params: dict[str, Any] = {
        **window_params(days),
        "label": label,
        "repo": repo,
        "state": state,
        "platform": "",
        "triaged": "",
        "granularity": granularity,
    }
    render_query_result(new_client().clickhouse_query("disabled_test_historical", params), output_json)


@log_app.command("url")
def log_url(job_id: int = typer.Argument(..., help="HUD job id.")) -> None:
    console.print(s3_log_url(job_id))


@log_app.command("download")
def log_download(
    job_id: int = typer.Argument(..., help="HUD job id."),
    output: Annotated[Path, typer.Option("--output", "-o", help="Path to write the raw log.")] = ...,
) -> None:
    console.print(str(download_log(job_id, output)))


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


@search_app.command("failures")
def search_failures(
    failure: str = typer.Argument(..., help="Failure text to search for."),
    repo: str | None = typer.Option("pytorch/pytorch", "--repo"),
    workflow_name: str | None = typer.Option(None, "--workflow-name"),
    branch_name: str | None = typer.Option(None, "--branch-name"),
    start_date: str | None = typer.Option(None, "--start-date"),
    end_date: str | None = typer.Option(None, "--end-date"),
    min_score: float = typer.Option(1.0, "--min-score"),
    days: int = typer.Option(7, "--days", min=1, max=90),
    output_json: bool = typer.Option(True, "--json/--table"),
) -> None:
    data = new_client().similar_failures(
        failure,
        repo,
        workflow_name,
        branch_name,
        start_date,
        end_date,
        min_score,
        days,
    )
    if output_json:
        print_json(data)
        return
    console.print(data)


@app.command("doctor")
def doctor() -> None:
    render_auth_doctor()


@auth_app.command("doctor")
def auth_doctor() -> None:
    render_auth_doctor()


def render_auth_doctor() -> None:
    config = load_config()
    status = gcx_status(config)
    table = Table(title="HUD auth")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("base_url", config.base_url)
    table.add_row("config", str(CONFIG_PATH))
    table.add_row("HUD_API_TOKEN/HUD_INTERNAL_BOT_TOKEN", "set" if config.internal_bot_token else "not set")
    table.add_row("GITHUB_TOKEN", "set" if config.github_token else "not set")
    table.add_row("GRAFANA_TOKEN", "set" if status.grafana_token_set else "not set")
    table.add_row("gcx", status.label)
    table.add_row("default", "browser impersonation without a token")
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
        console.print(result.stderr, end="", stderr=True)
    raise typer.Exit(result.returncode)


def new_client() -> HudClient:
    return HudClient(load_config())


def resolve_log_path(path: Path | None, job_id: int | None, output: Path | None) -> Path:
    if path and job_id:
        raise typer.BadParameter("use either --path or --job-id, not both")
    if path:
        return path
    if job_id is None:
        raise typer.BadParameter("provide --path or --job-id")
    return download_log(job_id, output or Path(f"hud-job-{job_id}.log"))


def parse_repo(repo: str) -> tuple[str, str]:
    parts = repo.split("/")
    if len(parts) != 2 or not all(parts):
        raise typer.BadParameter("repo must look like owner/name, e.g. pytorch/pytorch")
    return parts[0], parts[1]


def parse_params(values: list[str]) -> dict[str, Any]:
    return {key: parse_value(value) for key, value in split_params(values)}


def split_params(values: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for item in values:
        if "=" not in item:
            raise typer.BadParameter(f"parameter must look like key=value: {item}")
        key, value = item.split("=", 1)
        if not key:
            raise typer.BadParameter(f"parameter key cannot be empty: {item}")
        pairs.append((key, value))
    return pairs


def parse_value(value: str) -> Any:
    match value.lower():
        case "true":
            return True
        case "false":
            return False
        case "null" | "none":
            return None
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)
    try:
        return float(value)
    except ValueError:
        return value


def print_json(data: Any) -> None:
    console.print_json(json.dumps(data, indent=2, sort_keys=True, default=str))


def render_query_result(data: Any, output_json: bool) -> None:
    if output_json:
        print_json(data)
        return
    if isinstance(data, list) and data and isinstance(data[0], dict):
        render_rows(data)
        return
    if isinstance(data, dict):
        rows = data.get("data") or data.get("rows") or data.get("result")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            render_rows(rows)
            return
    console.print(data)


def render_rows(rows: list[dict[str, Any]]) -> None:
    table = Table(title="Query results")
    columns = list(rows[0].keys())
    for column in columns:
        table.add_column(str(column))
    for row in rows:
        table.add_row(*(str(row.get(column, "")) for column in columns))
    console.print(table)


def render_commits(commits: list[dict[str, Any]], show_jobs: bool) -> None:
    if not commits:
        console.print("No commits returned.")
        return
    table = Table(title="Recent HUD commits")
    table.add_column("SHA")
    table.add_column("Title")
    table.add_column("Author")
    table.add_column("Jobs")
    table.add_column("Failures")
    table.add_column("Pending")
    for commit in commits:
        counts = commit["job_counts"]
        table.add_row(
            str(commit.get("short_sha", "")),
            str(commit.get("title", ""))[:80],
            str(commit.get("author", ""))[:24],
            str(counts["total"]),
            str(counts["failure"]),
            str(counts["pending"]),
        )
        if show_jobs:
            for job in commit["jobs"][:20]:
                console.print(f"  - {job.get('name', 'unknown job')} ({job.get('id', 'unknown')})")
    console.print(table)


def run() -> int:
    try:
        app()
        return 0
    except (HudError, GcxError, LogError, QuerySourceError) as error:
        console.print(f"[red]error:[/red] {error}")
        return 1


if __name__ == "__main__":
    run()
