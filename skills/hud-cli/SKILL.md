# HUD CLI agent skill

Use this skill when answering PyTorch CI/CD questions with the local `hud` CLI. Treat `hud gcx` as the primary data path: authenticate through GitHub, query Grafana datasources, and keep outputs bounded.

## Core rules

- Prefer `hud gcx chq ... --json` for ClickHouse-backed agent workflows.
- If `gcx` is missing, run `hud gcx install`; do not suggest `uv tool install gcx` because `gcx` is a Go release binary.
- Use `hud gcx login` after `gh auth login`; it mints a Grafana token through HUD without printing it.
- Do not add user-facing direct HUD data API commands. Build common helpers on top of `hud gcx chq`, `hud gcx run`, `gh`, or raw log URLs.
- Do not print token values.
- Keep SQL windows and limits explicit for expensive queries.
- Start with schema discovery (`SHOW TABLES`, `DESCRIBE table`) when unsure.
- For multi-line SQL, write a `.sql` file and run `hud gcx chq --file query.sql --json`, or pipe with `hud gcx chq - --json`.

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
hud gcx install
hud gcx login
```

## ClickHouse through Grafana/gcx

```bash
# What tables exist?
hud gcx chq "SHOW TABLES FROM default" --json

# What columns are in the main CI job table?
hud gcx chq "DESCRIBE default.workflow_job" --json

# CI job outcomes over the last day
hud gcx chq "SELECT conclusion, count() AS n FROM default.workflow_job WHERE completed_at > now() - INTERVAL 1 DAY GROUP BY conclusion ORDER BY n DESC" --json

# Busiest workflows in the last 6 hours
hud gcx chq "SELECT workflow_name, count() AS jobs FROM default.workflow_job WHERE completed_at > now() - INTERVAL 6 HOUR GROUP BY workflow_name ORDER BY jobs DESC LIMIT 15" --json

# Recent failing jobs with classified failure text
hud gcx chq "SELECT id, workflow_name, name, tupleElement(torchci_classification, 'line') AS failure_line FROM default.workflow_job WHERE completed_at > now() - INTERVAL 6 HOUR AND conclusion = 'failure' ORDER BY completed_at DESC LIMIT 20" --json

# Recent jobs for a workflow/name pattern
hud gcx chq "SELECT id, completed_at, conclusion, name FROM default.workflow_job WHERE completed_at > now() - INTERVAL 12 HOUR AND workflow_name = 'pull' AND name ILIKE '%linux%' ORDER BY completed_at DESC LIMIT 50" --json
```

For larger queries:

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
