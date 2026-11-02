-- CI test hours per owner module for one main commit's post-merge pull + trunk runs.
-- Definition matches owner_test_time.sql (sum of test-attempt durations, files with several
-- owner labels count toward each owner), restricted to jobs whose head_sha = {sha}.
-- All job run attempts and in-job test reruns count, so a commit with retried jobs reads higher;
-- compare commits with the same job count (count() per head_sha in default.workflow_job).
-- Only commits pushed within the last {days} days are found (bounds the workflow_job scan);
-- ci.runs is then read only between the commit's first job start and last job end.
-- Pick a sha: SELECT head_sha, count() FROM default.workflow_job WHERE completed_at > now() - INTERVAL 2 DAY
--   AND repository_full_name = 'pytorch/pytorch' AND head_branch = 'main' AND workflow_name = 'trunk' GROUP BY head_sha
-- @param sha=1f4d2d5e820dd870626dca4f9603e3a49879dbde
-- @param days=14
WITH commit_jobs AS (
    SELECT id, started_at, completed_at
    FROM default.workflow_job
    WHERE completed_at > now() - INTERVAL {days:UInt32} DAY
      AND repository_full_name = 'pytorch/pytorch'
      AND head_branch = 'main'
      AND workflow_name IN ('pull', 'trunk')
      AND head_sha = {sha:String}
),
per_test AS (
    SELECT
        tests_id,
        sum(dateDiff('millisecond', started_at, ended_at)) AS ms,
        count() AS attempts
    FROM ci.runs
    WHERE started_at >= (SELECT min(started_at) FROM commit_jobs)
      AND started_at <= (SELECT max(completed_at) FROM commit_jobs)
      AND github_workflow_job_id IN (SELECT id FROM commit_jobs)
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
