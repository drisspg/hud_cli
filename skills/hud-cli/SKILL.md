# HUD CLI agent skill

Use this skill when answering PyTorch CI/CD or test questions with the local `hud` CLI. HUD authenticates everything through your `gh` session; never print token values.

## Pick the data path

1. **Tests API** (`hud tests ...`) for per-test questions: health, flakiness, failures, owners, runs of one test, tests on one commit. Responses carry `hud_url` links; give them to the user beside the results.
2. **ClickHouse** (`hud gcx chq ...`) when the API cannot answer: aggregates across tests, owners, jobs, workflows, shards, TTS metrics.
3. **Raw logs** (`hud log ...`) for one job's output.

## Setup and health

```bash
gh auth login --hostname github.com --git-protocol ssh --web
hud gcx login        # installs the pinned, checksum-verified gcx on first use
hud doctor
hud gcx doctor --json
```

HUD keeps its own gcx binary and config (`hud gcx doctor` shows the path); it ignores any `gcx` on PATH unless `GCX_PATH` is set.

## Tests API

```bash
hud tests schema                                  # endpoints
hud tests schema /api/tests                       # parameters for one endpoint
hud tests api /api/tests -q repo=pytorch/pytorch -q owner=nn -q status=flaky -q window=7d -q limit=20
hud tests api /api/tests -q repo=pytorch/pytorch -q search=test_flex_decoding -q sort=-mean_passed_duration_milliseconds -q limit=10
hud tests api /api/tests -q repo=pytorch/pytorch -q workflow_path=.github/workflows/periodic-strict.yml -q status=failing -q status=flaky
hud tests api /api/tests/test/TEST_ID
hud tests api /api/tests/test/TEST_ID/runs -q outcome=failed -q limit=20
hud tests api /api/tests/test/TEST_ID/runs -q group_by=commit -q window=3d
hud tests api /api/tests/commit/SHA -q repo=pytorch/pytorch -q outcome=failed
```

- Repeat `-q key=value` for multi-valued parameters.
- Add `--all --max-pages N` (default 10) to follow cursors and concatenate list fields; output includes `pages_fetched`.
- `/api/tests` defaults to `ran_on=main` and a 12h window; set `window` explicitly (3h, 12h, 1d, 3d, 7d, 14d).
- HUD page paths in `hud tests schema` (`kind=page`) are for humans; link them, never fetch them.

## ClickHouse through gcx

HUD only allows a single read-only statement (SELECT/WITH/SHOW/DESCRIBE/EXPLAIN); write/admin SQL is blocked before dispatch.

```bash
hud gcx tables                     # every non-system table with row counts
hud gcx tables -d ci
hud gcx describe ci.runs --json
hud gcx columns owner --json       # search names/comments across databases
hud gcx sample ci.tests --limit 5 --json
hud gcx chq "SELECT 1" --json
hud gcx chq --file query.sql --json
hud gcx chq --file query.sql -p owner=nn -p days=3 --json   # fill {owner:String}, {days:UInt32}
hud gcx chq --file query.sql --csv -o /tmp/rows.csv          # CSV/JSON to a file for plotting
hud gcx chq --file heavy.sql --max-seconds 180 --json        # default ClickHouse timeout is 60s
cat query.sql | hud gcx chq - --json
```

SQL files use ClickHouse-style typed placeholders `{name:Type}` (String, Int*/UInt*, Float*, Date), rendered client-side with safe quoting. Declare defaults as comment lines `-- @param name=value`; unknown `-p` names and missing values are errors. Timeouts surface as the ClickHouse error message (code 159): narrow the window before raising `--max-seconds`.

### Datasets

- `ci` — test data behind the Tests API. Use it for every test question.
  - `ci.runs`: one row per test attempt (~10B rows). Always filter `started_at` to a narrow window (start with 1 day). Duration is `dateDiff('millisecond', started_at, ended_at)`. Links to jobs via `github_workflow_job_id = default.workflow_job.id`.
  - `ci.tests`, `ci.environments`, `ci.env_vars`, `ci.owners`: read with `FINAL`, including in joins, or rows duplicate.
  - `ci.owners`: `(repo, file) -> owners Array(String)`, labels without the `module: ` prefix.
  - `ci.health`: precomputed per test/environment health by `ran_on` and `window`.
- `default` — GitHub webhook data (`workflow_job`, `workflow_run`, `push`, `pull_request`, ...) plus other CI tables. Filter `repository_full_name = 'pytorch/pytorch'` on `workflow_job`. Avoid `workflow_job.workflow_event` (restricted dictionary); use `workflow_run.event`.
- `misc` — `runner_cost` (daily $ per runner label; read with `FINAL`), `autorevert_events_v2`, `disabled_tests_historical`, queue/utilization tables.
- `tests` (`all_test_runs` and friends) — legacy; do not use.
- More query patterns: HUD's saved queries in `pytorch/test-infra` at `torchci/clickhouse_queries/<name>/query.sql`, schemas with column docs in `clickhouse_db_schema/`.

### Guardrails

- Always bound event/job/test tables by time and add `LIMIT` for row queries.
- Filter each side of a join by time/repo before joining; for `ci.runs`, prefilter jobs in a CTE and use `github_workflow_job_id IN (...)`.
- Prefer aggregates (`count`, `quantile`, `topK`) over raw rows; no `SELECT *` outside `hud gcx sample`.
- `pull_request` is snapshot-style: use `countDistinct(number)`.

### Test Week recipes

`examples/test_week/README.md` catalogs validated, parameterized SQL: TTGS/TTRS, trunk critical path, shard imbalance, job time breakdown, owner test time (current, compare, per commit), slowest tests per owner, OpInfo hot ops, duplicate coverage, skip waste, runner cost (per job and per owner), `@periodic` candidates and health, autorevert activity, and disabled-test cost. Start there before writing new SQL, and run them with `hud gcx chq --file ... -p name=value --json`.

## Other Grafana datasources

```bash
hud gcx run -- datasources list
hud gcx run -- datasources prometheus query -d grafanacloud-prom 'topk(10, sum by (name) (gha_assigned_jobs) - sum by (name) (gha_running_jobs))'
hud gcx run -- datasources prometheus query -d grafanacloud-prom 'sum by (cluster) (gha_running_jobs)' --since 6h --step 30m
```

Pass datasource UIDs (`grafanacloud-prom`, `ceczcsck1b20wb`) to `-d`, not display names. Use `hud gcx chq` rather than `gcx datasources clickhouse query`; the passthrough rejects it.

## Jobs and logs

```bash
hud trace JOB_ID                     # ./hud-job-JOB_ID.pftrace for https://ui.perfetto.dev
hud trace JOB_ID --no-log --no-links --json -o /tmp/t.pftrace   # ClickHouse only; no log download or GitHub fetches
hud trace --run RUN_ID --no-links      # all jobs of a workflow run on one clock, longest first; or pass several JOB_IDs
hud job JOB_ID --json
hud log url JOB_ID
hud log download JOB_ID              # saved under the hud-cli cache dir
hud log search 'RuntimeError|FAILED' --job-id JOB_ID --limit 20 --json
hud log patterns /path/job.log --json
hud log tests /path/job.log --json
hud log sections /path/job.log --start 'Traceback' --end '^$' --json
```
