# HUD CLI agent skill

Use this skill when answering PyTorch CI/CD questions with the local `hud` CLI. Treat `hud gcx` as the primary data path: authenticate through GitHub, query Grafana datasources, and keep outputs bounded.

## Core rules

- Prefer `hud gcx chq ... --json` for ClickHouse-backed agent workflows.
- Use `hud gcx login` after `gh auth login`; it mints a Grafana token through HUD without printing it.
- Do not add user-facing direct HUD data API commands. Build common helpers on top of `hud gcx chq`, `hud gcx run`, `gh`, or raw log URLs.
- Do not print token values.
- Keep SQL windows and limits explicit for expensive queries.

## Quick health checks

```bash
hud --help
hud auth setup
hud doctor
hud gcx doctor --json
```

## Authenticate

```bash
gh auth login --hostname github.com --git-protocol ssh --web
hud gcx login
```

## ClickHouse through Grafana/gcx

```bash
hud gcx chq "SHOW TABLES FROM default" --json
hud gcx chq "DESCRIBE default.workflow_job" --json
hud gcx chq "SELECT conclusion, count() AS n FROM default.workflow_job WHERE completed_at > now() - INTERVAL 1 DAY GROUP BY conclusion ORDER BY n DESC" --json
hud gcx chq "SELECT workflow_name, count() AS jobs FROM default.workflow_job WHERE completed_at > now() - INTERVAL 6 HOUR GROUP BY workflow_name ORDER BY jobs DESC LIMIT 15" --json
```

## Grafana/gcx passthrough

```bash
hud gcx run -- datasources list
hud gcx run -- metrics query -d grafanacloud-pytorchci-prom 'up' --from now-1h --to now
```

## Jobs and logs

```bash
hud job JOB_ID --json
hud log url JOB_ID
hud log download JOB_ID -o /tmp/job.log
hud log search 'RuntimeError|FAILED' --path /tmp/job.log --json
hud log search 'RuntimeError|FAILED' --job-id JOB_ID --limit 20 --json
hud log patterns /tmp/job.log --json
hud log tests /tmp/job.log --json
hud log sections /tmp/job.log --start 'Traceback' --end '^$' --json
```
