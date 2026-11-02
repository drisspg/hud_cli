-- What sets trunk time to green signal: for main commits whose pull and trunk jobs all
-- succeeded on the first attempt (same commit set as trunk_ttgs.sql), take the job that
-- finished last and aggregate by its job config (shard index stripped). For each config:
-- how often it was last, the median finish offset from the push (= that commit's TTGS),
-- and the last job's median runtime (started -> completed) and queue (created -> started).
-- Minutes. The gap between ttgs and queue + runtime is upstream time (mostly the build the
-- test job waited on, plus workflow scheduling).
-- Window: pushes from {days} days ago up to 1 day ago (lets slow commits finish).
-- @param days=7
-- @param repo=pytorch/pytorch
-- @param branch=main
-- @param limit=20
WITH commits AS (
    SELECT
        r.head_sha,
        min(r.created_at) AS pushed_at,
        argMax(
            (r.name, replaceRegexpOne(j.name, ', \\d+, \\d+, ', ', '), j.created_at, j.started_at, j.completed_at),
            j.completed_at
        ) AS last_job,
        countIf(j.conclusion NOT IN ('success', 'skipped')) AS non_green_jobs,
        uniqExact(r.name) AS workflows
    FROM default.workflow_run AS r
    INNER JOIN default.workflow_job AS j ON j.run_id = r.id
    WHERE r.created_at > now() - INTERVAL {days:UInt32} DAY
      AND r.created_at < now() - INTERVAL 1 DAY
      AND j.completed_at > now() - INTERVAL ({days:UInt32} + 1) DAY
      AND r.repository.full_name = {repo:String}
      AND r.head_branch = {branch:String}
      AND r.event = 'push'
      AND r.name IN ('pull', 'trunk')
      AND j.run_attempt = 1
    GROUP BY r.head_sha
    HAVING non_green_jobs = 0 AND workflows = 2
)
SELECT
    last_job.1 AS workflow_name,
    last_job.2 AS job_config,
    count() AS times_last,
    round(count() / any(total), 3) AS share_last,
    round(quantile(0.5)(dateDiff('second', pushed_at, last_job.5)) / 60, 1) AS p50_ttgs_min,
    round(quantile(0.5)(dateDiff('second', last_job.4, last_job.5)) / 60, 1) AS p50_runtime_min,
    round(quantile(0.5)(greatest(dateDiff('second', last_job.3, last_job.4), 0)) / 60, 1) AS p50_queue_min
FROM commits
CROSS JOIN (SELECT count() AS total FROM commits) AS totals
GROUP BY workflow_name, job_config
ORDER BY times_last DESC
LIMIT {limit:UInt32}
