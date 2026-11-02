-- Slowest test cases for one owner module on main (pull + trunk) over the last {days} days.
-- ci.owners stores labels without the "module: " prefix (owner=nn, not "module: nn").
-- @param days=1
-- @param owner=nn
WITH main_jobs AS (
    SELECT id
    FROM default.workflow_job
    WHERE completed_at > now() - INTERVAL {days:UInt32} DAY
      AND repository_full_name = 'pytorch/pytorch'
      AND head_branch = 'main'
      AND workflow_name IN ('pull', 'trunk')
),
owned_files AS (
    SELECT file
    FROM ci.owners FINAL
    WHERE repo = 'pytorch/pytorch' AND has(owners, {owner:String})
)
SELECT
    t.file,
    t.suite,
    t.case_name,
    round(sum(dateDiff('millisecond', r.started_at, r.ended_at)) / 1000, 1) AS total_seconds,
    round(avg(dateDiff('millisecond', r.started_at, r.ended_at)) / 1000, 2) AS mean_seconds,
    count() AS attempts
FROM ci.runs AS r
INNER JOIN ci.tests AS t FINAL ON r.tests_id = t.id
WHERE r.started_at > now() - INTERVAL {days:UInt32} DAY
  AND r.github_workflow_job_id IN (SELECT id FROM main_jobs)
  AND t.repo = 'pytorch/pytorch'
  AND t.file IN (SELECT file FROM owned_files)
GROUP BY t.file, t.suite, t.case_name
ORDER BY total_seconds DESC
LIMIT 50
