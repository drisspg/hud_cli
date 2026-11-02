-- Trunk time to green signal: main push to the last pull/trunk job finishing,
-- for commits whose pull and trunk jobs all succeeded on the first attempt. Minutes.
-- Commits that went green only after a rerun are excluded.
-- Window: pushes from {days} days ago up to 1 day ago (lets slow commits finish).
-- @param days=7
-- @param repo=pytorch/pytorch
-- @param branch=main
WITH commits AS (
    SELECT
        r.head_sha,
        min(r.created_at) AS pushed_at,
        max(j.completed_at) AS last_job_at,
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
    count() AS green_commits,
    round(quantile(0.5)(dateDiff('second', pushed_at, last_job_at)) / 60, 1) AS p50_ttgs_min,
    round(quantile(0.9)(dateDiff('second', pushed_at, last_job_at)) / 60, 1) AS p90_ttgs_min,
    round(avg(dateDiff('second', pushed_at, last_job_at)) / 60, 1) AS mean_ttgs_min
FROM commits
