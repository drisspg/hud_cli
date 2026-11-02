-- Shard imbalance for test jobs on main: the slowest shard sets time-to-signal.
-- Groups "test (config, shard, num_shards, runner)" jobs by workflow run and config.
-- Window: successful jobs completed in the last {days} days; {min_runs} filters noisy configs.
-- @param days=1
-- @param repo=pytorch/pytorch
-- @param branch=main
-- @param min_runs=3
WITH shards AS (
    SELECT
        run_id,
        workflow_name,
        replaceRegexpOne(name, ', \\d+, \\d+, ', ', ') AS job_config,
        toUInt32OrZero(extract(name, ', \\d+, (\\d+), ')) AS num_shards,
        dateDiff('second', started_at, completed_at) / 60 AS minutes
    FROM default.workflow_job
    WHERE completed_at > now() - INTERVAL {days:UInt32} DAY
      AND repository_full_name = {repo:String}
      AND head_branch = {branch:String}
      AND workflow_name IN ('pull', 'trunk')
      AND conclusion = 'success'
      AND match(name, ' / test \\(.*, \\d+, \\d+, ')
      AND NOT match(name, 'rerun_disabled_tests|mem_leak_check')
),
per_run AS (
    SELECT workflow_name, job_config, any(num_shards) AS num_shards,
        max(minutes) AS max_minutes, avg(minutes) AS mean_minutes
    FROM shards
    GROUP BY run_id, workflow_name, job_config
    HAVING count() > 1
)
SELECT
    workflow_name,
    job_config,
    any(num_shards) AS num_shards,
    round(quantile(0.5)(max_minutes), 1) AS p50_slowest_shard_min,
    round(quantile(0.5)(mean_minutes), 1) AS p50_mean_shard_min,
    round(quantile(0.5)(max_minutes - mean_minutes), 1) AS p50_excess_min,
    count() AS runs
FROM per_run
GROUP BY workflow_name, job_config
HAVING runs >= {min_runs:UInt32}
ORDER BY p50_excess_min DESC
LIMIT 30
