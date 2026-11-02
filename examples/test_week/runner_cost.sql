-- Estimated runner dollars per workflow and job config for post-merge pull + trunk on main.
-- misc.runner_cost is a daily rollup per repo/workflow/job/runner_type with no branch, so it
-- cannot isolate main directly. Instead derive a $/hour rate per runner_type from it
-- (sum(cost) / sum(duration hours), all repos, same window) and apply that rate to each main
-- job's wall time (started_at -> completed_at) on its first label (labels[1] is the runner type).
-- misc.runner_cost's own duration is exactly this wall time summed per labels[1] and completion
-- date (checked per runner_type for pytorch/pytorch pull + trunk), so the rates are consistent.
-- Job config strips the shard index: "... / test (default, 2, 5, runner)" -> "... / test (default, runner)".
-- All attempts and conclusions count: reruns and failures burn runners too.
-- Caveats: runner types priced at $0 or absent (AMD ROCm donated runners, *-l4 11-41 and
-- gb300 runners as of 2026-11) show up in unpriced_hours, not usd. Estimates, not invoices.
-- @param days=7
-- @param repo=pytorch/pytorch
-- @param branch=main
-- @param limit=40
WITH rates AS (
    SELECT runner_type, sum(cost) / sum(duration) AS usd_per_hour
    FROM misc.runner_cost
    WHERE date >= today() - {days:UInt32}
      AND duration > 0
    GROUP BY runner_type
    HAVING sum(cost) > 0
),
jobs AS (
    SELECT
        workflow_name,
        replaceRegexpOne(name, ', \\d+, \\d+, ', ', ') AS job_config,
        labels[1] AS runner_type,
        greatest(dateDiff('second', started_at, completed_at), 0) / 3600 AS hours
    FROM default.workflow_job
    WHERE completed_at > now() - INTERVAL {days:UInt32} DAY
      AND repository_full_name = {repo:String}
      AND head_branch = {branch:String}
      AND workflow_name IN ('pull', 'trunk')
      AND started_at > toDateTime('2000-01-01')
)
SELECT
    j.workflow_name AS workflow_name,
    j.job_config AS job_config,
    any(j.runner_type) AS runner_type,
    count() AS jobs,
    round(sum(j.hours), 1) AS runner_hours,
    round(sum(j.hours * r.usd_per_hour), 0) AS usd,
    round(sumIf(j.hours, r.runner_type = ''), 1) AS unpriced_hours
FROM jobs AS j
LEFT JOIN rates AS r ON r.runner_type = j.runner_type
GROUP BY workflow_name, job_config
ORDER BY usd DESC
LIMIT {limit:UInt32}
