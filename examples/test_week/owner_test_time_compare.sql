-- Test Week before/after: CI test hours per owner module on main post-merge pull + trunk runs,
-- last {days} days ("current") vs an equal-length baseline window ending
-- {baseline_offset_days} days ago. Defaults compare the last 3 days with the same weekdays a week
-- earlier; pass e.g. -p days=7 -p baseline_offset_days=7 once ci.runs covers both windows.
-- Definition matches owner_test_time.sql: sum of test-attempt durations (ms -> hours), each
-- run bucketed by its started_at; files with several owner labels count toward each owner.
-- Caveats: raw hours move with main commit volume (weekends, sev-driven reruns, backfills), so
-- read the share columns (owner hours / '__total__' hours) before crediting a change to test edits.
-- ci.runs history is short (began 2026-10-24); a baseline before that reads as zero. Check
-- coverage first with: SELECT toDate(started_at) d, count() FROM ci.runs WHERE started_at > ... GROUP BY d.
-- Baseline windows overlap the current one when baseline_offset_days < days.
-- @param days=3
-- @param baseline_offset_days=7
WITH
    now() - INTERVAL {days:UInt32} DAY AS current_start,
    now() - INTERVAL {baseline_offset_days:UInt32} DAY AS baseline_end,
    baseline_end - INTERVAL {days:UInt32} DAY AS baseline_start,
main_jobs AS (
    SELECT id
    FROM default.workflow_job
    WHERE completed_at > least(baseline_start, current_start)
      AND repository_full_name = 'pytorch/pytorch'
      AND head_branch = 'main'
      AND workflow_name IN ('pull', 'trunk')
),
per_test AS (
    SELECT
        tests_id,
        sumIf(dateDiff('millisecond', started_at, ended_at), started_at > current_start) AS current_ms,
        sumIf(dateDiff('millisecond', started_at, ended_at),
            started_at > baseline_start AND started_at <= baseline_end) AS baseline_ms
    FROM ci.runs
    WHERE (started_at > current_start OR (started_at > baseline_start AND started_at <= baseline_end))
      AND github_workflow_job_id IN (SELECT id FROM main_jobs)
    GROUP BY tests_id
),
per_owner AS (
    SELECT
        arrayJoin(arrayConcat(if(empty(o.owners), ['unowned'], o.owners), ['__total__'])) AS owner,
        sum(p.current_ms) / 3600000 AS current_hours,
        sum(p.baseline_ms) / 3600000 AS baseline_hours
    FROM per_test AS p
    INNER JOIN ci.tests AS t FINAL ON p.tests_id = t.id
    LEFT JOIN ci.owners AS o FINAL ON o.repo = t.repo AND o.file = t.file
    WHERE t.repo = 'pytorch/pytorch'
    GROUP BY owner
),
totals AS (
    SELECT current_hours AS total_current, baseline_hours AS total_baseline
    FROM per_owner
    WHERE owner = '__total__'
)
SELECT
    owner,
    round(current_hours, 1) AS current_hours,
    round(baseline_hours, 1) AS baseline_hours,
    round(current_hours - baseline_hours, 1) AS delta_hours,
    if(baseline_hours > 0, round(100 * (current_hours - baseline_hours) / baseline_hours, 1), NULL) AS pct_change,
    round(100 * current_hours / total_current, 2) AS current_share_pct,
    if(total_baseline > 0, round(100 * baseline_hours / total_baseline, 2), NULL) AS baseline_share_pct
FROM per_owner
CROSS JOIN totals
ORDER BY abs(delta_hours) DESC
LIMIT 40
