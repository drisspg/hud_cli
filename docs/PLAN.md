# HUD CLI plan

## Goal

Build a small, installable Python CLI that gives PyTorch engineers and agents one stable entry point for CI/CD questions:

- What is currently red on trunk?
- Which jobs are queued or stuck?
- Which tests are flaky or disabled?
- What failed for a job or workflow?
- What is time-to-land / time-to-signal over a window?
- Can an agent fetch concise, bounded JSON without raw credential friction?

## Blessed data path

Use Grafana `gcx` as the primary data path. HUD is used only to mint a short-lived Grafana token from GitHub auth:

```bash
gh auth login --hostname github.com --git-protocol ssh --web
hud gcx login
hud gcx chq "SHOW TABLES FROM default" --json
```

Do not add user-facing direct HUD data API commands. Common CI/CD helpers should compile down to one of:

- `hud gcx chq SQL` for ClickHouse through Grafana,
- `hud gcx run -- ...` for other Grafana datasources,
- `gh ...` for GitHub mechanics,
- `hud log ...` for raw S3 job logs.

## CLI shape

- `hud auth setup|doctor`
- `hud gcx doctor|login|chq|run`
- `hud job JOB_ID --json`
- `hud log url|download|search|patterns|tests|sections ...`

## Safety and overload controls

- Require explicit time windows for expensive ClickHouse helpers.
- Cap default limits and row counts.
- Prefer `--json` for agent-facing workflows.
- Avoid exposing raw credential values in output.
- Do not persist HUD-minted Grafana tokens in config, docs, logs, or generated files.

## Remaining follow-ups

- Add common helper commands on top of `hud gcx chq` for trunk health, queued jobs, slow tests, flaky tests, and time-to-signal.
- Validate live `hud gcx login` and `hud gcx chq` in an environment with `gcx` installed and GitHub auth.
- Add an MCP wrapper or generated `claude mcp add-json` helper once command names settle.
