from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from hud import __version__
from hud.auth import CONFIG_PATH, load_config
from hud.gcx import (
    GcxError,
    clickhouse_query,
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

app = typer.Typer(help="PyTorch CI data CLI backed by Grafana gcx.", no_args_is_help=True)
auth_app = typer.Typer(help="Inspect CLI authentication and setup.", no_args_is_help=True)
gcx_app = typer.Typer(help="Authenticate and query Grafana gcx.", no_args_is_help=True)
log_app = typer.Typer(help="Fetch and search raw CI job logs.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")
app.add_typer(gcx_app, name="gcx")
app.add_typer(log_app, name="log")
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


@log_app.command("url")
def log_url(job_id: int = typer.Argument(..., help="GitHub Actions job id.")) -> None:
    console.print(s3_log_url(job_id))


@log_app.command("download")
def log_download(
    job_id: int = typer.Argument(..., help="GitHub Actions job id."),
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
        "Run `hud gcx install` if gcx is missing, then `hud gcx login`. Login mints a short-lived Grafana token from HUD using your GitHub auth and runs `gcx login --yes pytorchci --server https://pytorchci.grafana.net --token ...` without printing the token.",
    )
    table.add_row(
        "ClickHouse",
        "Use `hud gcx chq SQL` after `hud gcx login`. Queries go through Grafana's ClickHouse datasource, not raw ClickHouse credentials.",
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
    try:
        result = login_with_hud_token(load_config(), token_name or hostname_token_name())
    except GcxError as error:
        console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    if result.stdout:
        console.print(result.stdout, end="")
    if result.stderr:
        err_console.print(result.stderr, end="")
    raise typer.Exit(result.returncode)


@gcx_app.command("chq")
def gcx_chq(
    sql: str = typer.Argument(..., help="ClickHouse SQL to run through Grafana's PyTorch datasource."),
    output_json: bool = typer.Option(False, "--json", help="Print parsed rows as JSON."),
    raw: bool = typer.Option(False, "--raw", help="Print raw Grafana datasource JSON."),
) -> None:
    try:
        data = clickhouse_query(load_config(), sql)
    except (GcxError, json.JSONDecodeError, KeyError) as error:
        console.print(f"[red]error:[/red] {error}")
        raise typer.Exit(1) from error
    if raw:
        print_json(data)
        return
    rows = rows_from_gcx_response(data)
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


def hostname_token_name() -> str:
    return socket.gethostname() or "hud-cli"


def resolve_log_path(path: Path | None, job_id: int | None, output: Path | None) -> Path:
    if path and job_id:
        raise typer.BadParameter("use either --path or --job-id, not both")
    if path:
        return path
    if job_id is None:
        raise typer.BadParameter("provide --path or --job-id")
    return download_log(job_id, output or Path(f"hud-job-{job_id}.log"))


def print_json(data: Any) -> None:
    console.print_json(json.dumps(data, indent=2, sort_keys=True, default=str))


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
    except (GcxError, LogError) as error:
        console.print(f"[red]error:[/red] {error}")
        return 1


if __name__ == "__main__":
    run()
