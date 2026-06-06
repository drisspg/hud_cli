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
hud gcx login
```

`hud gcx login` calls `https://hud.pytorch.org/api/gcx-token?token_name=$(hostname)` with your `gh auth token`, then runs `gcx login --yes pytorchci --server https://pytorchci.grafana.net --token ...` without printing the token.

Optional environment variables:

```bash
export HUD_BASE_URL=https://hud.pytorch.org/api
export GITHUB_TOKEN=...
export HUD_GCX_PATH=/path/to/gcx
```

`GITHUB_TOKEN` is used for GitHub-backed access; if it is not set, `hud` automatically tries `gh auth token`.

Optional config file at `~/.config/pytorch-hud/config.toml`:

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
```

## Grafana ClickHouse

Authenticate once:

```bash
hud gcx login
```

Run ClickHouse SQL through Grafana's PyTorch ClickHouse datasource:

```bash
hud gcx chq "SHOW TABLES FROM default"
hud gcx chq "DESCRIBE default.workflow_job"
hud gcx chq "SELECT conclusion, count() AS n FROM default.workflow_job WHERE completed_at > now() - INTERVAL 1 DAY GROUP BY conclusion ORDER BY n DESC"
hud gcx chq "SELECT workflow_name, count() AS jobs FROM default.workflow_job WHERE completed_at > now() - INTERVAL 6 HOUR GROUP BY workflow_name ORDER BY jobs DESC LIMIT 15"
```

Use JSON for agents:

```bash
hud gcx chq "SHOW TABLES FROM default" --json
```

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

- `hud gcx login` mints a Grafana token through HUD using GitHub auth and logs `gcx` into pytorchci.
- `hud gcx chq` runs ClickHouse SQL through Grafana's PyTorch ClickHouse datasource.
- `hud gcx run -- ...` shells out to `gcx` without printing credentials.
- `hud job` prints direct raw S3 job log URLs.
- `hud log ...` prints direct raw S3 log URLs, downloads logs, searches logs, extracts common patterns/test summaries, and filters bounded sections.
- `hud auth doctor` / `hud doctor` explain how the CLI is authenticating.

## Direction

This project intentionally prefers one blessed path over backward-compatible fallbacks while it is early. Build common CI/CD question helpers on top of `hud gcx chq` and `gcx` datasources rather than direct HUD rate-limited data endpoints.
