-- CI spent on rerun_disabled_tests jobs (the scheduled jobs that rerun disabled tests to see
-- whether they can be re-enabled), per workflow, over the last {days} full UTC days (today excluded).
-- jobs/hours: default.workflow_job FINAL on {branch}, any attempt, conclusion != 'skipped';
-- hours = wall time started_at -> completed_at.
-- cost_usd: misc.runner_cost FINAL (needs FINAL or rows duplicate); it has no branch column,
-- so cost covers every branch, and its newest date usually lags by about a day.
-- pct_of_workflow_cost: rerun_disabled_tests share of that workflow's total runner cost.
-- Runners without cost data (e.g. s390x) show cost_usd = 0.
-- @param days=7
-- @param branch=main
WITH jobs AS (
    SELECT
        workflow_name,
        count() AS jobs,
        round(sum(dateDiff('second', started_at, completed_at)) / 3600, 1) AS hours
    FROM default.workflow_job FINAL
    WHERE completed_at >= today() - {days:UInt32}
      AND completed_at < today()
      AND repository_full_name = 'pytorch/pytorch'
      AND head_branch = {branch:String}
      AND name LIKE '%rerun_disabled_tests%'
      AND conclusion != 'skipped'
    GROUP BY workflow_name
),
cost AS (
    SELECT
        workflow_name,
        sumIf(cost, job_name LIKE '%rerun_disabled_tests%') AS rerun_cost,
        sum(cost) AS workflow_cost
    FROM misc.runner_cost FINAL
    WHERE date >= today() - {days:UInt32}
      AND date < today()
      AND group_repo = 'pytorch/pytorch'
      AND workflow_name IN (SELECT workflow_name FROM jobs)
    GROUP BY workflow_name
)
SELECT
    j.workflow_name AS workflow,
    j.jobs AS jobs,
    j.hours AS hours,
    round(c.rerun_cost) AS cost_usd,
    round(100 * c.rerun_cost / nullIf(c.workflow_cost, 0), 1) AS pct_of_workflow_cost
FROM jobs AS j
LEFT JOIN cost AS c ON c.workflow_name = j.workflow_name
ORDER BY j.hours DESC
LIMIT 30
