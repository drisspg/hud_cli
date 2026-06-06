from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Any

import plotly.graph_objects as go
import typer

app = typer.Typer(no_args_is_help=True)


@app.command()
def main(
    input_json: Annotated[Path, typer.Argument(help="JSON row output from `hud gcx chq ... --json`.")],
    output_html: Annotated[Path, typer.Option("--output", "-o", help="HTML report path.")] = Path("slow-test-files.html"),
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 50,
) -> None:
    rows = json.loads(input_json.read_text())
    summaries = summarize(rows, limit)
    figure = go.Figure(
        go.Bar(
            x=[row["total_time"] for row in summaries],
            y=[row["file"] for row in summaries],
            orientation="h",
            customdata=[
                [row["max_time"], row["entry_count"], row["configs"]] for row in summaries
            ],
            hovertemplate=(
                "<b>%{y}</b><br>"
                "total avg runtime: %{x:.2f}s<br>"
                "slowest config: %{customdata[0]:.2f}s<br>"
                "test/config rows: %{customdata[1]}<br>"
                "configs:<br>%{customdata[2]}"
                "<extra></extra>"
            ),
        )
    )
    figure.update_layout(
        title=f"Slowest PyTorch test files — {len(rows)} total test/config rows",
        xaxis_title="Total average runtime across configs/jobs (seconds)",
        yaxis_title="Test file",
        yaxis={"autorange": "reversed"},
        height=max(650, 34 * len(summaries) + 190),
        margin={"l": 300, "r": 50, "t": 95, "b": 70},
    )
    output_html.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(output_html, include_plotlyjs="cdn", full_html=True)
    typer.echo(output_html)


def summarize(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_file[str(row.get("file") or "unknown")].append(row)

    summaries = []
    for file, file_rows in by_file.items():
        sorted_rows = sorted(file_rows, key=row_time, reverse=True)
        times = [row_time(row) for row in sorted_rows]
        summaries.append(
            {
                "file": file,
                "total_time": sum(times),
                "max_time": max(times) if times else 0.0,
                "entry_count": len(file_rows),
                "configs": "<br>".join(format_config(row) for row in sorted_rows[:10]),
            }
        )
    return sorted(summaries, key=lambda row: row["total_time"], reverse=True)[:limit]


def row_time(row: dict[str, Any]) -> float:
    return float(row.get("time") or 0)


def format_config(row: dict[str, Any]) -> str:
    return f"{row_time(row):.2f}s — {row.get('base_name') or 'unknown job'} / {row.get('test_config') or 'unknown config'}"


if __name__ == "__main__":
    app()
