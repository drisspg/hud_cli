-- Skip waste: test files on main where nearly every attempt over the last {days} days was
-- skipped, yet the file still ran in many jobs. Each job that runs a file pays its startup
-- (process spawn, imports, CUDA init, collection), which ci.runs does not record: jobs is the
-- number of those startups, and skipped_hours/run_hours only cover the test bodies.
-- Counts every main-branch workflow. A file qualifies with skip_pct >= {min_skip_pct}
-- and jobs >= {min_jobs}; skip_pct = skipped attempts / all attempts (xfailed counts as run).
-- Caveats: a file can be fully skipped on most configs for good reason (e.g. CUDA-only tests on
-- CPU shards); the fix is usually to exclude it from those configs rather than delete tests.
-- Check top_skip_reason (most common outcome_summary among skips) before acting.
-- Two passes over ci.runs: counting distinct jobs for every file exhausts server memory, so jobs
-- are counted only for files that already meet the skip threshold.
-- @param days=1
-- @param min_skip_pct=95
-- @param min_jobs=20
WITH main_jobs AS (
    SELECT id
    FROM default.workflow_job
    WHERE completed_at > now() - INTERVAL {days:UInt32} DAY
      AND repository_full_name = 'pytorch/pytorch'
      AND head_branch = 'main'
),
per_test AS (
    SELECT
        tests_id,
        count() AS attempts,
        countIf(outcome = 'skipped') AS skipped,
        sumIf(dateDiff('millisecond', started_at, ended_at), outcome = 'skipped') AS skipped_ms,
        sumIf(dateDiff('millisecond', started_at, ended_at), outcome != 'skipped') AS run_ms
    FROM ci.runs
    WHERE started_at > now() - INTERVAL {days:UInt32} DAY
      AND github_workflow_job_id IN (SELECT id FROM main_jobs)
    GROUP BY tests_id
),
mostly_skipped_files AS (
    SELECT
        t.file AS file,
        groupArray(t.id) AS test_ids,
        count() AS test_cases,
        sum(p.attempts) AS attempts,
        round(100 * sum(p.skipped) / sum(p.attempts), 2) AS skip_pct,
        round(sum(p.skipped_ms) / 3600000, 2) AS skipped_hours,
        round(sum(p.run_ms) / 3600000, 2) AS run_hours
    FROM per_test AS p
    INNER JOIN ci.tests AS t FINAL ON t.id = p.tests_id
    WHERE t.repo = 'pytorch/pytorch'
    GROUP BY file
    HAVING skip_pct >= {min_skip_pct:Float64}
),
file_jobs AS (
    SELECT
        f.file AS file,
        uniqExact(r.github_workflow_job_id) AS jobs,
        topKIf(1)(r.outcome_summary, r.outcome = 'skipped')[1] AS top_skip_reason
    FROM ci.runs AS r
    INNER JOIN (SELECT file, arrayJoin(test_ids) AS tests_id FROM mostly_skipped_files) AS f
        ON f.tests_id = r.tests_id
    WHERE r.started_at > now() - INTERVAL {days:UInt32} DAY
      AND r.github_workflow_job_id IN (SELECT id FROM main_jobs)
      AND r.tests_id IN (SELECT arrayJoin(test_ids) FROM mostly_skipped_files)
    GROUP BY file
    HAVING jobs >= {min_jobs:UInt32}
)
SELECT
    m.file,
    j.jobs,
    m.attempts,
    m.skip_pct,
    m.test_cases,
    m.skipped_hours,
    m.run_hours,
    j.top_skip_reason
FROM mostly_skipped_files AS m
INNER JOIN file_jobs AS j ON j.file = m.file
ORDER BY j.jobs DESC, m.attempts DESC
LIMIT 50
