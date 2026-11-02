-- Pull time to red signal: PR workflow start to the first failed job, first attempts only
-- (reruns would otherwise stretch the tail). Minutes. Expect a skewed distribution:
-- compare p50/p90 rather than the mean.
WITH failed_runs AS (
    SELECT
        r.id,
        min(r.created_at) AS started_at,
        minIf(j.completed_at, j.conclusion = 'failure') AS first_red_at
    FROM default.workflow_run AS r
    INNER JOIN default.workflow_job AS j ON j.run_id = r.id
    WHERE r.created_at > now() - INTERVAL 7 DAY
      AND j.completed_at > now() - INTERVAL 8 DAY
      AND r.repository.full_name = 'pytorch/pytorch'
      AND r.event = 'pull_request'
      AND r.name = 'pull'
      AND j.run_attempt = 1
    GROUP BY r.id
    HAVING countIf(j.conclusion = 'failure') > 0
)
SELECT
    count() AS red_runs,
    round(avg(dateDiff('second', started_at, first_red_at)) / 60, 1) AS mean_ttrs_min,
    round(quantile(0.5)(dateDiff('second', started_at, first_red_at)) / 60, 1) AS p50_ttrs_min,
    round(quantile(0.9)(dateDiff('second', started_at, first_red_at)) / 60, 1) AS p90_ttrs_min,
    round(stddevPop(dateDiff('second', started_at, first_red_at)) / 60, 1) AS stddev_ttrs_min
FROM failed_runs
