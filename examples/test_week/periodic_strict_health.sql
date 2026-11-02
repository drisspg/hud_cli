-- Health of periodic-strict, the workflow behind the @periodic decorator.
-- Failures here auto-revert, so tests moved to @periodic must not be flaky.
SELECT
    name,
    count() AS jobs,
    countIf(conclusion = 'failure') AS failures,
    round(avg(dateDiff('second', started_at, completed_at)) / 60, 1) AS mean_minutes,
    max(completed_at) AS last_completed_at
FROM default.workflow_job
WHERE completed_at > now() - INTERVAL 7 DAY
  AND repository_full_name = 'pytorch/pytorch'
  AND workflow_name = 'periodic-strict'
  AND conclusion != 'skipped'
GROUP BY name
ORDER BY failures DESC, jobs DESC
LIMIT 30
