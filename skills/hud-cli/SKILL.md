# HUD CLI agent skill

Use this skill when answering PyTorch CI/CD questions with the local `hud` CLI. Treat `hud` as a primitive data-access CLI: fetch bounded JSON, inspect query metadata, then do analysis/plots/reports in normal scripts. Do not add one-off features to `hud` unless the workflow is broadly reusable.

## Core rules

- Prefer `--json` for agent workflows.
- Keep result sizes bounded with `--per-page`, `--limit`, `--days`, or explicit query params.
- Do not print token values.
- Do not ask for tokens first. Run `hud auth doctor` only when access fails or auth state matters.
- Prefer `gcx` for Grafana datasource queries and `gh` for GitHub workflow operations instead of rebuilding those APIs inside `hud`.
- If HUD returns 401/403, mention VPN, `HUD_INTERNAL_BOT_TOKEN`, or proxy access.
- If HUD returns 429, say the live service rate-limited the request; keep local scripts/tests mocked.
- If `gcx` is needed, require a read-only `GRAFANA_TOKEN` and never store or echo it.

## Quick health checks

```bash
hud --help
hud auth doctor
hud gcx doctor --json
```

## HUD commit status

Raw HUD payload:

```bash
hud status main --repo pytorch/pytorch --per-page 10 --json
```

Normalized compact payload for analysis:

```bash
hud status main --failures --compact-json
hud status main --failures --job-regex linux --compact-json
hud status main --pending --compact-json
```

## Historical failure search

Use this to answer “has this failed before?” or “which jobs saw similar failures?”

```bash
hud search failures 'CUDA out of memory' --repo pytorch/pytorch --branch-name main --days 14 --json
hud search failures 'PACKAGES DO NOT MATCH THE HASHES' --workflow-name linux --min-score 2 --json
```

## Jobs and logs

```bash
hud job JOB_ID --artifacts --log-url --json
hud log url JOB_ID
hud log download JOB_ID -o /tmp/job.log
hud log search 'RuntimeError|FAILED' --path /tmp/job.log --json
hud log search 'RuntimeError|FAILED' --job-id JOB_ID --limit 20 --json
hud log patterns /tmp/job.log --json
hud log tests /tmp/job.log --json
hud log sections /tmp/job.log --start 'Traceback' --end '^$' --json
```

## Curated recipes

```bash
hud recipe queued --json
hud recipe trunk-red --days 7 --json
hud recipe flaky-test test_name --days 7 --json
hud recipe disabled-tests --days 30 --json
hud recipe tts --days 7 --json
hud recipe tts --percentile --days 7 --json
hud recipe slow-test-files --limit 100 --json
hud recipe slow-test-files --periodic --limit 100 --json
```

## Raw HUD ClickHouse queries

Inspect what exists:

```bash
hud query list
hud query explain queued_jobs --json
hud query source test_time_per_file --json
hud query source flaky_tests/across_jobs --no-params --text
```

Run a saved query:

```bash
hud query run queued_jobs --json
hud query run flaky_tests/across_jobs -p startTime=2026-06-01T00:00:00 -p stopTime=2026-06-05T00:00:00 --json
```

## Grafana/gcx passthrough

Use this for Grafana-backed data sources, dashboards, Prometheus, and ClickHouse-through-Grafana workflows:

```bash
hud gcx doctor --json
hud gcx run -- datasources list
hud gcx run -- metrics query -d grafanacloud-pytorchci-prom 'up' --from now-1h --to now
```

## Workflow: make an interactive Plotly report for slowest test files

Do not add a plotting feature to `hud` for one-off reports. Fetch JSON with `hud`, then write a small script.

1. Fetch the data:

```bash
hud recipe slow-test-files --limit 500 --json > /tmp/slow-test-files.json
```

Use periodic jobs instead of recent strict commits if that better matches the question:

```bash
hud recipe slow-test-files --periodic --limit 500 --json > /tmp/slow-test-files.json
```

2. Generate a Plotly HTML report:

```bash
uv run python - <<'PY'
import json
from collections import defaultdict
from pathlib import Path

import plotly.graph_objects as go

rows = json.loads(Path('/tmp/slow-test-files.json').read_text())
by_file = defaultdict(list)
for row in rows:
    by_file[row.get('file') or 'unknown'].append(row)

summary = []
for file, file_rows in by_file.items():
    total_time = sum(float(row.get('time') or 0) for row in file_rows)
    max_time = max(float(row.get('time') or 0) for row in file_rows)
    configs = sorted(file_rows, key=lambda row: float(row.get('time') or 0), reverse=True)
    summary.append({
        'file': file,
        'total_time': total_time,
        'max_time': max_time,
        'entry_count': len(file_rows),
        'configs': '<br>'.join(
            f"{float(row.get('time') or 0):.2f}s — {row.get('base_name') or 'unknown'} / {row.get('test_config') or 'unknown'}"
            for row in configs[:10]
        ),
    })

summary = sorted(summary, key=lambda row: row['total_time'], reverse=True)[:50]
fig = go.Figure(go.Bar(
    x=[row['total_time'] for row in summary],
    y=[row['file'] for row in summary],
    orientation='h',
    customdata=[[row['max_time'], row['entry_count'], row['configs']] for row in summary],
    hovertemplate=(
        '<b>%{y}</b><br>'
        'total time: %{x:.2f}s<br>'
        'slowest config: %{customdata[0]:.2f}s<br>'
        'test/config rows: %{customdata[1]}<br>'
        'configs:<br>%{customdata[2]}'
        '<extra></extra>'
    ),
))
fig.update_layout(
    title=f"Slowest PyTorch test files — {len(rows)} total rows",
    xaxis_title='Total average runtime across configs/jobs (seconds)',
    yaxis_title='Test file',
    yaxis={'autorange': 'reversed'},
    height=max(600, 30 * len(summary) + 180),
    margin={'l': 260, 'r': 40, 't': 90, 'b': 60},
)
fig.write_html('/tmp/slow-test-files.html', include_plotlyjs='cdn', full_html=True)
print('/tmp/slow-test-files.html')
PY
```

3. Open it:

```bash
open /tmp/slow-test-files.html
```

If `plotly` is not available in the active environment, install it in the scratch environment you are using for the report; do not add it as a `hud` dependency for a one-off plot.

## Answer pattern

1. State the exact `hud` command used.
2. Summarize the key signal.
3. Include IDs, SHAs, job names, file names, timings, and links if present.
4. Say when data is incomplete because auth, VPN, endpoint support, missing `gcx`, rate limiting, or query parameters blocked access.
