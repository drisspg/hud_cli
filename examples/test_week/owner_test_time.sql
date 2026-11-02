-- CI test time per owner module for post-merge pull + trunk runs on main.
-- ci.runs has billions of rows: keep the window small (default 1 day) and widen deliberately.
-- Files with several owner labels count toward each owner.
-- Runs are summed per test before joining ci.tests/ci.owners; joining raw runs is ~20x slower.
-- @param days=1
WITH main_jobs AS (
    SELECT id
    FROM default.workflow_job
    WHERE completed_at > now() - INTERVAL {days:UInt32} DAY
      AND repository_full_name = 'pytorch/pytorch'
      AND head_branch = 'main'
      AND workflow_name IN ('pull', 'trunk')
),
per_test AS (
    SELECT
        tests_id,
        sum(dateDiff('millisecond', started_at, ended_at)) AS ms,
        count() AS attempts
    FROM ci.runs
    WHERE started_at > now() - INTERVAL {days:UInt32} DAY
      AND github_workflow_job_id IN (SELECT id FROM main_jobs)
    GROUP BY tests_id
)
SELECT
    arrayJoin(if(empty(o.owners), ['unowned'], o.owners)) AS owner,
    round(sum(p.ms) / 3600000, 2) AS test_hours,
    sum(p.attempts) AS attempts,
    countDistinct(t.file) AS files
FROM per_test AS p
INNER JOIN ci.tests AS t FINAL ON p.tests_id = t.id
LEFT JOIN ci.owners AS o FINAL ON o.repo = t.repo AND o.file = t.file
WHERE t.repo = 'pytorch/pytorch'
GROUP BY owner
ORDER BY test_hours DESC
LIMIT 40
