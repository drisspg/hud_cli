-- CI test time per owner module for post-merge pull + trunk runs on main.
-- ci.runs has billions of rows: keep the window small (1 day here) and widen deliberately.
-- Files with several owner labels count toward each owner.
WITH main_jobs AS (
    SELECT id
    FROM default.workflow_job
    WHERE completed_at > now() - INTERVAL 1 DAY
      AND repository_full_name = 'pytorch/pytorch'
      AND head_branch = 'main'
      AND workflow_name IN ('pull', 'trunk')
)
SELECT
    arrayJoin(if(empty(o.owners), ['unowned'], o.owners)) AS owner,
    round(sum(dateDiff('millisecond', r.started_at, r.ended_at)) / 3600000, 2) AS test_hours,
    count() AS attempts,
    countDistinct(t.file) AS files
FROM ci.runs AS r
INNER JOIN ci.tests AS t FINAL ON r.tests_id = t.id
LEFT JOIN ci.owners AS o FINAL ON o.repo = t.repo AND o.file = t.file
WHERE r.started_at > now() - INTERVAL 1 DAY
  AND r.github_workflow_job_id IN (SELECT id FROM main_jobs)
  AND t.repo = 'pytorch/pytorch'
GROUP BY owner
ORDER BY test_hours DESC
LIMIT 40
