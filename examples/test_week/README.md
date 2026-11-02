# Test Week queries

Bounded, parameterized ClickHouse queries for PyTorch CI speedup work. Every file runs with no arguments; override defaults with `-p name=value`:

```bash
uv run hud gcx chq --file examples/test_week/periodic_candidates.sql --json
uv run hud gcx chq --file examples/test_week/slowest_tests_for_owner.sql -p owner=inductor -p days=3 --json
uv run hud gcx chq --file examples/test_week/runner_cost.sql --csv -o /tmp/runner-cost.csv
```

Each file's header documents its exact definition, window, and caveats. Runtimes are measured with defaults (2026-11-02).

## Signal metrics

| File | Answers | Params (default) | Runtime |
| --- | --- | --- | --- |
| `trunk_ttgs.sql` | Main push → last pull/trunk job, commits green on first attempt (p50/p90/mean) | `days=7`, `repo`, `branch` | ~1s |
| `pull_ttrs.sql` | PR workflow start → first failed job, first attempts (p50/p90/mean/stddev) | `days=7`, `repo`, `workflow=pull` | ~1s |
| `trunk_critical_path.sql` | Which job finishes last on green main commits, with finish offset, runtime, queue | `days=7`, `repo`, `branch`, `limit` | ~1-3s |
| `shard_imbalance.sql` | Slowest vs mean shard per test job config | `days=1`, `repo`, `branch`, `min_runs=3` | ~1s |
| `job_time_breakdown.sql` | Queue, runtime, main step (`Test`/`Build`) vs other steps per job config | `days=3`, `repo`, `branch`, `job_regex`, `min_jobs`, `limit` | ~2s |

## Where test time goes

| File | Answers | Params (default) | Runtime |
| --- | --- | --- | --- |
| `owner_test_time.sql` | Test hours per owner module on main pull + trunk | `days=1` | ~2-6s |
| `owner_test_time_compare.sql` | Owner test hours vs an equal-length baseline window, with share-of-total columns | `days=3`, `baseline_offset_days=7` | ~11-22s |
| `owner_test_time_for_commit.sql` | Owner test hours for one main commit's post-merge pull + trunk jobs | `sha`, `days=14` | ~1-4s |
| `slowest_tests_for_owner.sql` | Slowest test cases for one owner | `days=1`, `owner=nn` | ~2s |
| `opinfo_slowest.sql` | OpInfo ops ranked by total time (op parsed from `case_name`) | `days=1`, `file_regex` | ~4s |
| `duplicate_coverage.sql` | Tests that ran (not skipped) in many configurations | `days=1`, `min_configs=10` | ~6-17s |
| `skip_waste.sql` | Files that are ≥95% skipped yet run in many jobs, with the top skip reason | `days=1`, `min_skip_pct=95`, `min_jobs=20` | ~7-26s |

## Cost

| File | Answers | Params (default) | Runtime |
| --- | --- | --- | --- |
| `runner_cost.sql` | Estimated USD and runner hours per workflow/job config on main | `days=7`, `repo`, `branch`, `limit` | ~3s |
| `runner_cost_by_owner.sql` | Job cost apportioned to owners by their share of test time | `days=1`, `repo`, `branch`, `limit` | ~5s |
| `disabled_rerun_cost.sql` | Hours and USD in `rerun_disabled_tests` jobs per workflow | `days=7`, `branch=main` | ~8-23s |

## @periodic and disabled tests

| File | Answers | Params (default) | Runtime |
| --- | --- | --- | --- |
| `periodic_candidates.sql` | Slow pull/trunk tests with zero failures and flakes on main: `@periodic` candidates | `window=14d`, `owner=all`, `min_mean_seconds=5` | ~1-5s |
| `periodic_strict_health.sql` | Job failures in `periodic-strict`, the `@periodic` workflow | `days=7` | ~1s |
| `periodic_autorevert.sql` | Autorevert reverts/restarts per workflow and triggering signal | `days=30`, `workflow=all` | ~1s |
| `disabled_tests_by_owner.sql` | Currently disabled tests per owner, and how many rerun all-green | `days=7`, `owner=all` | ~1-2s |

## Data caveats

- `ci.runs` starts on 2026-10-24, and volume swings day to day; for before/after comparisons prefer share-of-total columns over raw hours.
- `misc.runner_cost` is a daily rollup with no branch column; cost queries derive per-runner-label $/hour and apply it to main jobs. ROCm, `*-11-41-l4`, gb300, and s390x runners are unpriced ($0, reported as `unpriced_hours` where relevant).
- Read `misc.runner_cost` and `default.workflow_job` with `FINAL` (or count distinct ids) when exact counts matter; replicated rows occasionally duplicate.
- `default.workflow_job.workflow_event` reads a restricted dictionary and fails under Grafana auth; join `workflow_run.event` instead.
- `ci.runs` does not record per-file startup cost, so `skip_waste.sql` reports job counts as the proxy.
- `owner_test_time_for_commit.sql`'s default `sha` ages out; its header shows how to pick a fresh one.
