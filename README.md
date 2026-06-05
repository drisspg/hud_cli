# pytorch-hud-cli

`hud` is a Typer CLI for PyTorch CI/CD data from HUD, HUD-backed ClickHouse queries, and existing PyTorch data tools such as Grafana `gcx`. The goal is to make questions about trunk health, failures, flaky tests, queues, time-to-land, and test history accessible to PyTorch engineers and to coding agents.

This is intentionally a catch-all workflow CLI, not a replacement for every data source. Use `gcx` for Grafana datasource access, `gh` for GitHub workflow mechanics, and HUD APIs for HUD-specific CI convenience.

## Install

```bash
uv tool install git+https://github.com/drisspg/pytorch-hud-cli.git
hud --help
```

For local development:

```bash
cd hud
uv run hud --help
uv run --extra dev pytest
```

## Authentication

The default path is tokenless HUD access using browser impersonation. If HUD blocks a request, the CLI tells you what to try next.

Optional environment variables:

```bash
export HUD_BASE_URL=https://hud.pytorch.org/api
export HUD_INTERNAL_BOT_TOKEN=...
export GITHUB_TOKEN=...
export GRAFANA_TOKEN=...
export HUD_GCX_PATH=/path/to/gcx
```

Optional config file at `~/.config/pytorch-hud/config.toml`:

```toml
[hud]
base_url = "https://hud.pytorch.org/api"
internal_bot_token = "..."
github_token = "..."
gcx_path = "/path/to/gcx"
```

`GRAFANA_TOKEN` should be a read-only token. `hud` only reports whether it is set and never prints the value.

Check the active auth path:

```bash
hud auth doctor
```

## Examples

Recent trunk commits:

```bash
hud status main --repo pytorch/pytorch
```

Recent trunk failures:

```bash
hud status main --failures
```

Normalized bounded JSON for an agent:

```bash
hud status main --failures --job-regex linux --compact-json
```

Raw HUD JSON for an agent:

```bash
hud status main --per-page 25 --json
```

Search historical failures with HUD/OpenSearch:

```bash
hud search failures 'CUDA out of memory' --repo pytorch/pytorch --branch-name main --days 14 --json
hud search failures 'PACKAGES DO NOT MATCH THE HASHES' --workflow-name linux --min-score 2 --json
```

Run a curated recipe:

```bash
hud recipe queued
hud recipe trunk-red --days 7
hud recipe flaky-test test_name --days 7
hud recipe disabled-tests --days 30
hud recipe tts --days 7
hud recipe tts --percentile --days 7
hud recipe slow-test-files --limit 20
hud recipe slow-test-files --periodic --limit 20
```

Run a named ClickHouse query:

```bash
hud query run queued_jobs
```

Pass query parameters:

```bash
hud query run flaky_tests/across_jobs -p startTime=2026-06-01T00:00:00 -p stopTime=2026-06-05T00:00:00
```

Inspect query metadata from the local catalog and `pytorch/test-infra` sources:

```bash
hud query list
hud query explain queued_jobs --json
hud query source queued_jobs --json
hud query source flaky_tests/across_jobs --no-params --text
```

Inspect a job:

```bash
hud job 123456789 --artifacts --log-url --json
```

Fetch and search raw job logs:

```bash
hud log url 123456789
hud log download 123456789 -o /tmp/job.log
hud log search 'RuntimeError|FAILED' --path /tmp/job.log --json
hud log search 'RuntimeError|FAILED' --job-id 123456789 --limit 20 --json
hud log patterns /tmp/job.log --json
hud log tests /tmp/job.log --json
hud log sections /tmp/job.log --start 'Traceback' --end '^$' --json
```

Check Grafana `gcx` availability and token state:

```bash
hud gcx doctor
hud gcx doctor --json
```

Pass through to `gcx` when you want Grafana-backed data sources:

```bash
hud gcx run -- datasources list
hud gcx run -- metrics query -d grafanacloud-pytorchci-prom 'up' --from now-1h --to now
```

## Initial commands

- `hud status` reads the HUD commit grid and can emit normalized `--compact-json` with status/name/failure filters.
- `hud job` reads job metadata and optionally artifacts/log URL.
- `hud search failures` searches historical HUD failures with bounded windows.
- `hud log ...` prints direct raw S3 log URLs, downloads logs, searches logs, extracts common patterns/test summaries, and filters bounded sections.
- `hud recipe ...` runs curated bounded workflows for common PyTorch CI questions.
- `hud query run` runs any named HUD ClickHouse query exposed by the HUD API.
- `hud query list`, `hud query explain`, and `hud query source` show local and `pytorch/test-infra` query metadata.
- `hud query examples` prints known useful query shapes.
- `hud auth doctor` explains how the CLI is authenticating.
- `hud gcx doctor` checks whether Grafana `gcx` and `GRAFANA_TOKEN` are available.
- `hud gcx run -- ...` safely shells out to `gcx` without printing credentials.

## Direction

See `docs/PLAN.md` for the implementation plan, `docs/COVERAGE.md` for the upstream parity audit, and `skills/hud-cli/SKILL.md` for the agent-facing usage contract.
