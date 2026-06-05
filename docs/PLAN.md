# HUD CLI plan

## Goal

Build a small, installable Python CLI that gives PyTorch engineers and agents one stable entry point for CI/CD questions:

- What is currently red on trunk?
- Which jobs are queued or stuck?
- Which tests are flaky or disabled?
- What failed for a commit, PR, job, or workflow?
- What is time-to-land / time-to-signal over a window?
- Can Claude or another agent fetch concise, bounded JSON without credentials friction?
- Can the workflow reuse `gcx` for Grafana datasources and `gh` for GitHub Actions instead of rebuilding those tools?

## Starting point

- Use Typer for the CLI and Rich for human-readable output.
- Use `uv`/`hatchling` packaging so the tool works with `uv run` during development and `uv tool install` globally.
- Start from HUD's public API shape:
  - `GET /api/hud/{owner}/{repo}/{branch_or_sha}/{page}`
  - `GET /api/clickhouse/{query_name}?parameters=<json>`
  - job/artifact/log endpoints as they stabilize.
- Borrow design ideas from the existing HUD MCP and ClickHouse MCP projects, but keep this repo a standalone CLI first.
- Treat PyTorch `ci-infra/grafana`'s `gcx` workflow as the preferred path for Grafana datasource queries across ClickHouse, Prometheus, and dashboard-backed sources.

## Authentication strategy

1. Default to tokenless HUD access with browser impersonation.
2. If present, send `HUD_INTERNAL_BOT_TOKEN` as `x-hud-internal-bot`.
3. If present, send `GITHUB_TOKEN` for any GitHub-backed discovery later.
4. Make auth inspectable with `hud auth doctor`.
5. For Grafana, prefer a read-only `GRAFANA_TOKEN` consumed by `gcx`; only report whether it is set.
6. Later, add a proxy-backed auth mode that uses SSO or GitHub auth and hides ClickHouse/HUD credentials from local agents.

## CLI shape

### Human commands

- `hud status [branch-or-sha] --repo pytorch/pytorch --failures --compact-json`
- `hud job JOB_ID --artifacts --log-url`
- `hud search failures ...`
- `hud log url|download|search|patterns|tests|sections ...`
- `hud query run QUERY_NAME -p key=value`
- `hud query examples`
- `hud auth doctor`
- `hud gcx doctor`
- `hud gcx run -- ...`

### Agent-oriented additions

- Add `--json` everywhere.
- Add bounded defaults: small `per_page`, explicit limits, no unbounded query helpers.
- Add normalized filtered status output for jobs by status, name regex, and failure regex.
- Add named recipes: `hud recipe trunk-red`, `hud recipe queued`, `hud recipe flaky-test TEST`, `hud recipe tts --days 7`.
- Add query metadata/source inspection commands for local catalog and `pytorch/test-infra` sources.

## Data model milestones

1. Thin client around HUD API and named ClickHouse queries.
2. Query catalog checked into the repo for common PyTorch CI questions.
3. Parameter schema for each query with examples and validation.
4. Output normalizers for commits, jobs, tests, queues, and time-series rows.
5. MCP wrapper or generated tool manifest that shells out to the CLI.
6. `gcx` passthrough for Grafana-backed datasources rather than duplicating Grafana query APIs.
7. Optional hosted proxy for seamless auth and rate limiting.

## Safety and overload controls

- Require explicit time windows for expensive query recipes.
- Cap default limits and row counts.
- Print the exact query name and parameters when running recipes.
- Add friendly errors for missing VPN, blocked HUD, bad query parameters, or proxy auth required.
- Avoid exposing raw credential values in output.
- Prefer read-only `GRAFANA_TOKEN` for agent access through `gcx`.
- Do not persist Grafana tokens in config, docs, logs, or generated files.

## Remaining follow-ups

- Confirm whether a stable HUD job metadata endpoint exists beyond artifacts and direct raw S3 logs.
- Add an MCP wrapper or generated `claude mcp add-json` helper once command names settle.
- Validate live HUD and `gcx` paths in an environment with HUD access, `gcx`, and read-only `GRAFANA_TOKEN`.
