-- Estimated runner dollars per owner module for post-merge pull + trunk test jobs on main.
-- Each job's cost is its wall hours times a $/hour rate for its runner type (labels[1]),
-- derived from misc.runner_cost as in runner_cost.sql. That cost is then split across the
-- tests the job ran in proportion to their ci.runs time, so setup/teardown overhead and
-- parallel test processes are absorbed proportionally. A file with several owner labels
-- splits its dollars evenly across them, so owner dollars add up to the attributed total.
-- Caveats: only jobs with ci.runs rows are attributed (build, lint, and jobs without test
-- reports are not); unpriced runners (AMD ROCm, *-l4 11-41, gb300) contribute $0 but their
-- test hours still count; ci.runs
-- is billions of rows, so keep {days} small and widen deliberately.
-- @param days=1
-- @param repo=pytorch/pytorch
-- @param branch=main
-- @param limit=40
WITH rates AS (
    SELECT runner_type, sum(cost) / sum(duration) AS usd_per_hour
    FROM misc.runner_cost
    WHERE date >= today() - {days:UInt32} - 7
      AND duration > 0
    GROUP BY runner_type
    HAVING sum(cost) > 0
),
main_jobs AS (
    SELECT
        j.id AS job_id,
        greatest(dateDiff('second', j.started_at, j.completed_at), 0) / 3600 * r.usd_per_hour AS usd
    FROM default.workflow_job AS j
    LEFT JOIN rates AS r ON r.runner_type = j.labels[1]
    WHERE j.completed_at > now() - INTERVAL {days:UInt32} DAY
      AND j.repository_full_name = {repo:String}
      AND j.head_branch = {branch:String}
      AND j.workflow_name IN ('pull', 'trunk')
      AND j.started_at > toDateTime('2000-01-01')
),
job_test_ms AS (
    SELECT github_workflow_job_id AS job_id, sum(dateDiff('millisecond', started_at, ended_at)) AS ms
    FROM ci.runs
    WHERE started_at > now() - INTERVAL ({days:UInt32} + 1) DAY
      AND github_workflow_job_id IN (SELECT job_id FROM main_jobs)
    GROUP BY job_id
    HAVING ms > 0
),
usd_per_ms AS (
    SELECT m.job_id AS job_id, m.usd / t.ms AS rate
    FROM main_jobs AS m
    INNER JOIN job_test_ms AS t ON t.job_id = m.job_id
),
per_test AS (
    SELECT
        r.tests_id AS tests_id,
        sum(dateDiff('millisecond', r.started_at, r.ended_at) * u.rate) AS usd,
        sum(dateDiff('millisecond', r.started_at, r.ended_at)) AS ms
    FROM ci.runs AS r
    INNER JOIN usd_per_ms AS u ON u.job_id = r.github_workflow_job_id
    WHERE r.started_at > now() - INTERVAL ({days:UInt32} + 1) DAY
      AND r.github_workflow_job_id IN (SELECT job_id FROM usd_per_ms)
    GROUP BY tests_id
)
SELECT
    owner,
    round(sum(usd / owner_count), 0) AS usd,
    round(sum(ms / owner_count) / 3600000, 1) AS test_hours,
    countDistinct(file) AS files
FROM (
    SELECT
        if(empty(o.owners), ['unowned'], o.owners) AS owner_list,
        length(owner_list) AS owner_count,
        t.file AS file,
        p.usd AS usd,
        p.ms AS ms
    FROM per_test AS p
    INNER JOIN ci.tests AS t FINAL ON p.tests_id = t.id
    LEFT JOIN ci.owners AS o FINAL ON o.repo = t.repo AND o.file = t.file
    WHERE t.repo = {repo:String}
)
ARRAY JOIN owner_list AS owner
GROUP BY owner
ORDER BY usd DESC
LIMIT {limit:UInt32}
