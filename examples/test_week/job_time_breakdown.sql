-- Where a job's time goes, per job config on post-merge pull + trunk for main:
-- queue (created_at -> started_at), total runtime (started_at -> completed_at), the main step
-- ("Test" for test jobs, "Build" for build jobs, from the workflow_job steps array), and
-- everything else on the runner (setup, checkout, docker pull, artifact upload, teardown).
-- Job config strips the shard index: "... / test (default, 2, 5, runner)" -> "... / test (default, runner)".
-- Window: successful jobs completed in the last {days} days, all attempts; {job_regex} is an
-- RE2 filter on the job name (default matches everything).
-- Caveats: a test job's created_at is when GitHub queued it after its build finished, so queue
-- is runner wait, not commit-to-start. Jobs without a main step (e.g. skipped builds that
-- reuse an old wheel) count as other_min = runtime.
-- @param days=3
-- @param repo=pytorch/pytorch
-- @param branch=main
-- @param job_regex=.
-- @param min_jobs=10
-- @param limit=40
WITH jobs AS (
    SELECT
        workflow_name,
        if(match(name, ' / build'), 'build', 'test') AS kind,
        replaceRegexpOne(name, ', \\d+, \\d+, ', ', ') AS job_config,
        greatest(dateDiff('second', created_at, started_at), 0) / 60 AS queue_min,
        dateDiff('second', started_at, completed_at) / 60 AS runtime_min,
        arraySum(arrayMap(
            s -> if(
                tupleElement(s, 'name') = if(kind = 'build', 'Build', 'Test')
                    AND tupleElement(s, 'started_at') > toDateTime('2000-01-01'),
                greatest(dateDiff('second', tupleElement(s, 'started_at'), tupleElement(s, 'completed_at')), 0),
                0),
            steps)) / 60 AS main_step_min
    FROM default.workflow_job
    WHERE completed_at > now() - INTERVAL {days:UInt32} DAY
      AND repository_full_name = {repo:String}
      AND head_branch = {branch:String}
      AND workflow_name IN ('pull', 'trunk')
      AND conclusion = 'success'
      AND started_at > toDateTime('2000-01-01')
      AND match(name, ' / (build$|build \\(|test \\()')
      AND match(name, {job_regex:String})
)
SELECT
    workflow_name,
    kind,
    job_config,
    count() AS jobs,
    round(quantile(0.5)(queue_min), 1) AS p50_queue_min,
    round(quantile(0.9)(queue_min), 1) AS p90_queue_min,
    round(quantile(0.5)(runtime_min), 1) AS p50_runtime_min,
    round(quantile(0.5)(main_step_min), 1) AS p50_main_step_min,
    round(quantile(0.5)(runtime_min - main_step_min), 1) AS p50_other_min,
    round(sum(main_step_min) / sum(runtime_min), 2) AS main_step_share
FROM jobs
GROUP BY workflow_name, kind, job_config
HAVING jobs >= {min_jobs:UInt32}
ORDER BY p50_queue_min + p50_runtime_min DESC
LIMIT {limit:UInt32}
