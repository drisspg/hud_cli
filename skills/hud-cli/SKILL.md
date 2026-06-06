# HUD CLI agent skill

Use this skill when answering PyTorch CI/CD questions with the local `hud` CLI. Treat `hud gcx` as the primary data path: authenticate through GitHub, query Grafana datasources, and keep outputs bounded.

## Core rules

- Prefer `hud gcx chq ... --json` for ClickHouse-backed agent workflows.
- If `gcx` is missing, run `hud gcx install`; do not suggest `uv tool install gcx` because `gcx` is a Go release binary.
- Use `hud gcx login` after `gh auth login`; it mints a Grafana token through HUD without printing it.
- Do not add user-facing direct HUD data API commands. Build common helpers on top of `hud gcx chq`, `hud gcx run`, `gh`, or raw log URLs.
- Do not print token values.
- Start with schema discovery (`hud gcx tables`, `hud gcx describe TABLE`, `hud gcx columns PATTERN`, `hud gcx sample TABLE`) when unsure.
- For multi-line SQL, write a `.sql` file and run `hud gcx chq --file query.sql --json`, or pipe with `hud gcx chq - --json`.

## Query guardrails

- Always add a bounded time window for event/job/test tables, e.g. `completed_at > now() - INTERVAL 6 HOUR` or `created_at > now() - INTERVAL 7 DAY`.
- Always add `LIMIT` for exploratory row queries.
- Do not run `SELECT *` except through `hud gcx sample TABLE --limit N` for structural inspection.
- Avoid unbounded joins across `workflow_job`, `workflow_run`, `test_run_s3`, and `pull_request`; filter each side by time/repo before joining.
- Prefer aggregate queries (`count`, `countDistinct`, `quantile`, `topK`) over downloading raw rows.
- Beware duplicate/snapshot-style tables such as `pull_request`; use `countDistinct(number)` for counts and validate row cardinality before drawing conclusions.
- Prefer `test_run_summary` for file/class-level test aggregates; use `test_run_s3` only when per-test rows are necessary.
- Do not rely on materialized/default columns that require dictionaries if Grafana read-only auth rejects them; explicitly join source tables instead.

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
hud gcx tables --json

# What columns are in the main CI job table?
hud gcx describe workflow_job --json

# Search table/column/comment metadata
hud gcx columns test --json
hud gcx columns torchci --table workflow_job --json

# Look at a few rows once a table looks relevant
hud gcx sample workflow_job --limit 5 --json

# CI job outcomes over the last day
hud gcx chq "SELECT conclusion, count() AS n FROM default.workflow_job WHERE completed_at > now() - INTERVAL 1 DAY GROUP BY conclusion ORDER BY n DESC" --json

# Busiest workflows in the last 6 hours
hud gcx chq "SELECT workflow_name, count() AS jobs FROM default.workflow_job WHERE completed_at > now() - INTERVAL 6 HOUR GROUP BY workflow_name ORDER BY jobs DESC LIMIT 15" --json

# Recent failing jobs with classified failure text
hud gcx chq "SELECT id, workflow_name, name, tupleElement(torchci_classification, 'line') AS failure_line FROM default.workflow_job WHERE completed_at > now() - INTERVAL 6 HOUR AND conclusion = 'failure' ORDER BY completed_at DESC LIMIT 20" --json

# Recent jobs for a workflow/name pattern
hud gcx chq "SELECT id, completed_at, conclusion, name FROM default.workflow_job WHERE completed_at > now() - INTERVAL 12 HOUR AND workflow_name = 'pull' AND name ILIKE '%linux%' ORDER BY completed_at DESC LIMIT 50" --json

# Active/incomplete jobs by workflow
hud gcx chq "SELECT workflow_name, countIf(status != 'completed') AS active_jobs, count() AS jobs FROM default.workflow_job WHERE created_at > now() - INTERVAL 6 HOUR GROUP BY workflow_name HAVING active_jobs > 0 ORDER BY active_jobs DESC LIMIT 10" --json

# Runtime percentiles by workflow
hud gcx chq "SELECT workflow_name, quantile(0.5)(dateDiff('second', started_at, completed_at)) / 60 AS p50_runtime_min, quantile(0.9)(dateDiff('second', started_at, completed_at)) / 60 AS p90_runtime_min, count() AS jobs FROM default.workflow_job WHERE completed_at > now() - INTERVAL 1 DAY AND started_at > toDateTime64(0, 9) GROUP BY workflow_name ORDER BY jobs DESC LIMIT 10" --json

# Time to failure signal: workflow creation to failed job completion
hud gcx chq "SELECT j.workflow_name, quantile(0.5)(dateDiff('second', r.created_at, j.completed_at)) / 60 AS p50_signal_min, quantile(0.9)(dateDiff('second', r.created_at, j.completed_at)) / 60 AS p90_signal_min, count() AS failed_jobs FROM default.workflow_job j INNER JOIN default.workflow_run r ON j.run_id = r.id WHERE j.completed_at > now() - INTERVAL 1 DAY AND j.conclusion = 'failure' AND r.created_at > toDateTime64(0, 9) GROUP BY j.workflow_name ORDER BY failed_jobs DESC LIMIT 10" --json

# OSS PR intake by GitHub author association
hud gcx chq "SELECT author_association, countDistinct(number) AS prs FROM default.pull_request WHERE tupleElement(base, 'repo').full_name = 'pytorch/pytorch' AND parseDateTimeBestEffort(created_at) > now() - INTERVAL 7 DAY GROUP BY author_association ORDER BY prs DESC" --json

# Revert commits on main
hud gcx chq "SELECT count() AS revert_commits FROM default.push ARRAY JOIN push.commits AS commit WHERE push.repository.full_name = 'pytorch/pytorch' AND push.ref = 'refs/heads/main' AND commit.timestamp > now() - INTERVAL 7 DAY AND commit.message ILIKE 'Revert %'" --json
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
