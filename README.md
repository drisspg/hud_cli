# hud_cli

`hud` is a Typer CLI for PyTorch CI/CD data built around Grafana `gcx`, GitHub `gh`, and raw CI logs. The goal is to make questions about trunk health, failures, flaky tests, queues, time-to-land, and test history accessible to PyTorch engineers and coding agents.

The blessed data path is Grafana `gcx`: HUD mints a Grafana token from your GitHub auth, then `gcx` queries Grafana datasources such as ClickHouse.

## Install

```bash
uv tool install git+https://github.com/drisspg/hud_cli.git
hud --help
```

For local development:

```bash
cd hud
uv run hud --help
uv run --extra dev pytest
```

## Authentication

Primary setup:

```bash
gh auth login --hostname github.com --git-protocol ssh --web
hud gcx install
hud gcx login
```

`hud gcx install` downloads the managed Grafana `gcx` release binary if `gcx` is not already on PATH. `hud gcx login` calls `https://hud.pytorch.org/api/gcx-token?token_name=$(hostname)` with your `gh auth token`, then runs `gcx login --yes pytorchci --server https://pytorchci.grafana.net --token ...` without printing the token.

Optional environment variables:

```bash
export HUD_BASE_URL=https://hud.pytorch.org/api
export GITHUB_TOKEN=...
export GCX_PATH=/path/to/gcx
```

`GITHUB_TOKEN` is used for GitHub-backed access; if it is not set, `hud` automatically tries `gh auth token`.

Optional config file at `~/.config/hud-cli/config.toml`:

```toml
[hud]
base_url = "https://hud.pytorch.org/api"
github_token = "..."
gcx_path = "/path/to/gcx"
```

`hud` only reports whether tokens are set and never prints their values.

Print setup instructions:

```bash
hud auth setup
```

Check active auth/tooling:

```bash
hud doctor
hud gcx doctor --json
hud gcx install
```

## Grafana ClickHouse

Install and authenticate once:

```bash
hud gcx install
hud gcx login
```

Discover available ClickHouse data before writing custom queries:

```bash
hud gcx tables --json
hud gcx describe workflow_job --json
hud gcx columns test --json
hud gcx columns torchci --table workflow_job --json
hud gcx sample workflow_job --limit 5 --json
```

`sample` is for structural inspection. For recent or meaningful rows, use `hud gcx chq` with an explicit `WHERE` and `ORDER BY`.

```bash
hud gcx chq "SELECT id, completed_at, conclusion, workflow_name, name FROM default.workflow_job WHERE completed_at > now() - INTERVAL 1 HOUR ORDER BY completed_at DESC LIMIT 5" --json
```

Run read-only ClickHouse SQL through Grafana's PyTorch ClickHouse datasource. HUD blocks write/admin SQL such as `INSERT`, `ALTER`, `DROP`, `TRUNCATE`, `OPTIMIZE`, `SYSTEM`, and multi-statement queries before sending them to Grafana.

```bash
hud gcx chq "SELECT conclusion, count() AS n FROM default.workflow_job WHERE completed_at > now() - INTERVAL 1 DAY GROUP BY conclusion ORDER BY n DESC"
hud gcx chq "SELECT workflow_name, count() AS jobs FROM default.workflow_job WHERE completed_at > now() - INTERVAL 6 HOUR GROUP BY workflow_name ORDER BY jobs DESC LIMIT 15"
```

Use JSON for agents:

```bash
hud gcx chq "SHOW TABLES FROM default" --json
```

Keep larger queries in files or pipe them through stdin:

```bash
cat > /tmp/jobs.sql <<'SQL'
SELECT workflow_name, count() AS jobs
FROM default.workflow_job
WHERE completed_at > now() - INTERVAL 6 HOUR
GROUP BY workflow_name
ORDER BY jobs DESC
LIMIT 15
SQL
hud gcx chq --file /tmp/jobs.sql --json
cat /tmp/jobs.sql | hud gcx chq - --json
```

Common starting points:

```bash
# What tables exist?
hud gcx tables --json

# What columns are in the main CI job table?
hud gcx describe workflow_job --json

# Search table/column/comment metadata
hud gcx columns test --json
hud gcx columns torchci --table workflow_job --json

# CI job outcomes over the last day
hud gcx chq "SELECT conclusion, count() AS n FROM default.workflow_job WHERE completed_at > now() - INTERVAL 1 DAY GROUP BY conclusion ORDER BY n DESC" --json

# Busiest workflows in the last 6 hours
hud gcx chq "SELECT workflow_name, count() AS jobs FROM default.workflow_job WHERE completed_at > now() - INTERVAL 6 HOUR GROUP BY workflow_name ORDER BY jobs DESC LIMIT 15" --json

# Recent failing jobs with failure text
hud gcx chq "SELECT id, workflow_name, name, tupleElement(torchci_classification, 'line') AS failure_line FROM default.workflow_job WHERE completed_at > now() - INTERVAL 6 HOUR AND conclusion = 'failure' ORDER BY completed_at DESC LIMIT 20" --json

# Active/incomplete jobs by workflow
hud gcx chq "SELECT workflow_name, countIf(status != 'completed') AS active_jobs, count() AS jobs FROM default.workflow_job WHERE created_at > now() - INTERVAL 6 HOUR GROUP BY workflow_name HAVING active_jobs > 0 ORDER BY active_jobs DESC LIMIT 10" --json

# Time to failure signal: workflow creation to failed job completion
hud gcx chq "SELECT j.workflow_name, quantile(0.5)(dateDiff('second', r.created_at, j.completed_at)) / 60 AS p50_signal_min, quantile(0.9)(dateDiff('second', r.created_at, j.completed_at)) / 60 AS p90_signal_min, count() AS failed_jobs FROM default.workflow_job j INNER JOIN default.workflow_run r ON j.run_id = r.id WHERE j.completed_at > now() - INTERVAL 1 DAY AND j.conclusion = 'failure' AND r.created_at > toDateTime64(0, 9) GROUP BY j.workflow_name ORDER BY failed_jobs DESC LIMIT 10" --json

# OSS PR intake by author association
hud gcx chq "SELECT author_association, countDistinct(number) AS prs FROM default.pull_request WHERE tupleElement(base, 'repo').full_name = 'pytorch/pytorch' AND parseDateTimeBestEffort(created_at) > now() - INTERVAL 7 DAY GROUP BY author_association ORDER BY prs DESC" --json

# Revert commits on main
hud gcx chq "SELECT count() AS revert_commits FROM default.push ARRAY JOIN push.commits AS commit WHERE push.repository.full_name = 'pytorch/pytorch' AND push.ref = 'refs/heads/main' AND commit.timestamp > now() - INTERVAL 7 DAY AND commit.message ILIKE 'Revert %'" --json
```

Query guardrails:

- Always use explicit time windows and `LIMIT` for exploration.
- Prefer aggregates over raw row dumps.
- Avoid `SELECT *` except through `hud gcx sample TABLE --limit N`.
- Treat snapshot/event tables such as `pull_request` carefully; use `countDistinct(number)` for PR counts.
- Prefer `test_run_summary` for test-file aggregates; use `test_run_s3` only for per-test detail.

Pass through to `gcx` when you want any Grafana-backed datasource directly:

```bash
hud gcx run -- datasources list
hud gcx run -- metrics query -d grafanacloud-pytorchci-prom 'up' --from now-1h --to now
```

## Raw CI logs

```bash
hud job 123456789 --json
hud log url 123456789
hud log download 123456789 -o /tmp/job.log
hud log search 'RuntimeError|FAILED' --path /tmp/job.log --json
hud log search 'RuntimeError|FAILED' --job-id 123456789 --limit 20 --json
hud log patterns /tmp/job.log --json
hud log tests /tmp/job.log --json
hud log sections /tmp/job.log --start 'Traceback' --end '^$' --json
```

## Commands

- `hud gcx install` installs the managed Grafana `gcx` binary when `gcx` is not already available.
- `hud gcx login` mints a Grafana token through HUD using GitHub auth and logs `gcx` into pytorchci.
- `hud gcx tables`, `hud gcx describe`, `hud gcx columns`, and `hud gcx sample` discover available ClickHouse data.
- `hud gcx chq` runs ClickHouse SQL through Grafana's PyTorch ClickHouse datasource.
- `hud gcx run -- ...` shells out to `gcx` without printing credentials.
- `hud job` prints direct raw S3 job log URLs.
- `hud log ...` prints direct raw S3 log URLs, downloads logs, searches logs, extracts common patterns/test summaries, and filters bounded sections.
- `hud auth doctor` / `hud doctor` explain how the CLI is authenticating.

## Direction

This project intentionally prefers one blessed path over backward-compatible fallbacks while it is early. Build common CI/CD question helpers on top of `hud gcx chq` and `gcx` datasources rather than direct HUD rate-limited data endpoints.
