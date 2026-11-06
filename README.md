# hud_cli

`hud` is a Typer CLI for PyTorch CI/CD data built around HUD's Tests API, Grafana `gcx`, GitHub `gh`, and raw CI logs. The goal is to make questions about trunk health, failures, flaky tests, queues, time-to-land, and test history accessible to PyTorch engineers and coding agents.

Two data paths, both authenticated by your `gh` session:

- `hud tests` calls HUD's Tests API for per-test health, runs, owners, and HUD page links.
- `hud gcx` mints a Grafana token through HUD, then queries Grafana datasources such as ClickHouse and Prometheus.

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

HUD manages a pinned `gcx` release (currently v1.7.1): the first gcx command downloads it, verifies its SHA-256 against the release checksums, and keeps it under the hud-cli data dir. Any `gcx` on PATH is ignored unless `GCX_PATH` points at it. `hud gcx install` pre-fetches the binary.

`hud gcx login` calls `https://hud.pytorch.org/api/gcx-token?token_name=$(hostname)` with your `gh auth token`, then runs `gcx login --yes pytorchci --server https://pytorchci.grafana.net` with the token in `GRAFANA_TOKEN`, so it never appears in process arguments. HUD points gcx at its own config file (`GCX_CONFIG`, under the hud-cli config dir) with `GCX_KEYCHAIN=off`, so it neither touches your personal gcx contexts nor needs an OS keychain. The stored token is a read-only Viewer token in a 0600 file.

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

## Tests API

HUD's Tests API answers standard test questions (health, flakiness, owners, runs of a test, tests on a commit). Every response carries `hud_url` links to the matching HUD page.

```bash
hud tests schema                    # endpoints from /api/openapi.json
hud tests schema /api/tests         # parameters for one endpoint
hud tests api /api/tests -q repo=pytorch/pytorch -q owner=nn -q status=flaky -q window=7d -q limit=20
hud tests api /api/tests/test/TEST_ID/runs -q group_by=commit -q window=3d
hud tests api /api/tests/commit/SHA -q repo=pytorch/pytorch -q outcome=failed
```

Requests go to HUD's `/api/authed/...` routes with your GitHub token; the unprefixed routes answer CLI callers with a bot challenge. Repeat `-q` for multi-valued parameters; add `--all --max-pages N` to follow pagination cursors and concatenate results.

## Grafana ClickHouse

Discover available ClickHouse data before writing custom queries. `tables` and `columns` span every non-system database unless you pass `-d`:

```bash
hud gcx tables
hud gcx tables -d ci --json
hud gcx describe ci.runs --json
hud gcx columns owner --json
hud gcx columns torchci -d default --table workflow_job --json
hud gcx sample workflow_job --limit 5 --json
```

Key datasets:

- `ci`: the test data behind the Tests API. `ci.runs` has one row per test attempt (~10B rows; always filter `started_at`). Read `ci.tests`, `ci.environments`, `ci.env_vars`, and `ci.owners` with `FINAL`.
- `default`: GitHub webhook data such as `workflow_job`, `workflow_run`, `push`, and `pull_request`.
- `tests` (`all_test_runs`): legacy; use `ci` instead.

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

SQL files can declare typed placeholders such as `{owner:String}` or `{days:UInt32}`, with defaults in `-- @param name=value` comment lines; fill them with `-p name=value`. SELECT/WITH queries run with a 60s `max_execution_time` (`--max-seconds` to change). Use `--csv` and/or `-o FILE` to export rows.

```bash
hud gcx chq --file examples/test_week/slowest_tests_for_owner.sql -p owner=nn -p days=1 --json
hud gcx chq --file examples/test_week/owner_test_time.sql --csv -o /tmp/owner-time.csv
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
- Answer test questions from the `ci` database (or `hud tests`), not the legacy `tests` database.

Validated, parameterized Test Week queries (TTGS/TTRS, critical path, shard imbalance, owner test time, OpInfo hot ops, duplicate coverage, skip waste, runner cost, `@periodic` candidates, disabled-test cost) are cataloged in [`examples/test_week/README.md`](examples/test_week/README.md).

Pass through to `gcx` when you want any Grafana-backed datasource directly:

```bash
hud gcx run -- datasources list
hud gcx run -- datasources prometheus query -d grafanacloud-prom 'sum by (cluster) (gha_running_jobs)' --since 6h --step 30m
```

Pass datasource UIDs (`grafanacloud-prom`, `ceczcsck1b20wb`) to `-d`; gcx 1.x does not resolve display names. Run ClickHouse SQL through `hud gcx chq`; the passthrough rejects `gcx datasources clickhouse query` so read-only enforcement cannot be bypassed.

## Job timelines in Perfetto

```bash
hud trace 123456789                       # writes ./hud-job-123456789.pftrace
hud trace 123456789 --no-log --json -o /tmp/job.pftrace
hud trace 123456789 123456790 --no-links  # several jobs on one clock: ./hud-jobs-123456789-2.pftrace
hud trace --run 42182634514 --no-links    # every job of a workflow run (longest --max-jobs 30): ./hud-run-42182634514.pftrace
hud trace 127785071136 128053214944 --align  # compare runs from different times: both start running at t=0 + longest queue
```

The output is a Perfetto protobuf trace (`.pftrace`); open it at https://ui.perfetto.dev. The `timeline` tracks nest every span inside the span that contains it, so each slice stack reads coarse to fine (the slice category names the level):

- `job` (queued, running) and `step` (GitHub steps, second resolution) from `default.workflow_job`.
- From the raw log (100 ns timestamps per line, kept at millisecond resolution): `action` (`##[group]Run ...` composite-action sections), `command` (every `set -x` line, including `++` subshell commands, with `xtrace_depth`), `model` (dynamo benchmark `cuda train <model>` runs from the loading bar onward, with the `pass`/`1.23x`/`eager_fail_to_run` result and enclosing command), and `process` (run_test.py `Running`/`Finished`, including import/collection).
- From `ci.runs` (millisecond resolution): `file` (one span per file report) and `test` (test cases). When `ci.runs` has no rows for a job (XPU, for example), `hud trace` reads its `test-reports-*_<job id>.zip` GitHub artifact through `gh api` instead: JUnit XML records each suite's start and each case's duration, so cases are laid back to back (pytest-xdist suites are packed onto estimated workers). The summary's `test_cases_source` says which was used; pass `--no-artifacts` to skip. Processes that never finish (timeouts) write no XML, so they stay at process granularity.

Separate tracks mark `idle in main step` (Test/Build time with no test case running) and `log silent outside tests >=30s` (log gaps outside test processes, test cases, and benchmark models, with the enclosing command in `during`; run_test.py buffers test output, so the log is always quiet while tests run).

Clicked slices carry links that Perfetto renders as clickable: `source` (the test's `def` line, test file, script, or torchbench model at the job's commit) and `github_log` (the GitHub step log). Test-file contents come from raw.githubusercontent.com; pass `--no-links` to skip fetching.

Several jobs share one wall clock under a `jobs` group, longest (queue plus run) first, each named `<minutes>m <job name> (<job id>)`. A job no runner ever picked up shows as one `queued (never picked up)` slice (GitHub sets its `started_at` at creation). Concurrent or partially overlapping spans spill onto numbered `timeline N` tracks; a span overrunning its parent by under 1s (GitHub step times are whole seconds) is clipped to the parent instead. A span nests only under a coarser category (job > step > action > command > model/process > file > test). Benchmark jobs upload no test reports, so their finest level is the log.

## Raw CI logs

```bash
hud job 123456789 --json
hud log url 123456789
hud log download 123456789                # saved under the hud-cli cache dir
hud log download 123456789 -o /tmp/job.log
hud log search 'RuntimeError|FAILED' --path /tmp/job.log --json
hud log search 'RuntimeError|FAILED' --job-id 123456789 --limit 20 --json
hud log patterns /tmp/job.log --json
hud log tests /tmp/job.log --json
hud log sections /tmp/job.log --start 'Traceback' --end '^$' --json
```

## Commands

- `hud tests api` / `hud tests schema` query HUD's Tests API and summarize its OpenAPI schema.
- `hud gcx install` pre-fetches HUD's pinned, checksum-verified `gcx` (other gcx commands install it on demand).
- `hud gcx login` mints a Grafana token through HUD using GitHub auth and logs `gcx` into pytorchci.
- `hud gcx tables`, `hud gcx describe`, `hud gcx columns`, and `hud gcx sample` discover available ClickHouse data.
- `hud gcx chq` runs ClickHouse SQL through Grafana's PyTorch ClickHouse datasource.
- `hud gcx run -- ...` shells out to `gcx` without printing credentials.
- `hud trace` writes a nested Perfetto `.pftrace` of one job's queue, steps, log commands, test processes, test files, and test cases.
- `hud job` prints direct raw S3 job log URLs.
- `hud log ...` prints direct raw S3 log URLs, downloads logs, searches logs, extracts common patterns/test summaries, and filters bounded sections.
- `hud auth doctor` / `hud doctor` explain how the CLI is authenticating.

## Direction

This project intentionally prefers one blessed path over backward-compatible fallbacks while it is early. Use `hud tests` for per-test questions the Tests API covers, and build other CI/CD helpers on `hud gcx chq` and `gcx` datasources.
