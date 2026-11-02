-- Currently disabled tests per owner module, with what rerun_disabled_tests saw for them.
-- Disabled set: misc.disabled_tests_historical FINAL on its newest day (one row per disable
-- issue, name like "test_foo_cuda (__main__.TestFooCUDA)").
-- Owner: the name is split into (case_name, suite) and matched to ci.tests (any file) and
-- ci.owners; disabled tests with no ci.tests match count as 'unmatched', matched files
-- without owners as 'unowned'. Files with several owner labels count toward each owner.
-- Rerun columns: default.rerun_disabled_tests rows from pytorch/pytorch workflow runs created in the
-- last {days} days, matched on (name, last component of classname); about half of disabled
-- tests match. all_green = rerun at least once and never red or flaky: re-enable candidates.
-- Reruns are not split by platform, so a platform-scoped disable can look green from other platforms.
-- @param days=7
-- @param owner=all
WITH disabled AS (
    SELECT
        name,
        issueNumber AS issue,
        extract(name, '^(\\S+) \\(') AS case_name,
        replaceRegexpOne(extract(name, '\\(([^)]*)\\)$'), '^.*\\.', '') AS suite
    FROM misc.disabled_tests_historical FINAL
    WHERE day = (SELECT max(day) FROM misc.disabled_tests_historical)
),
files AS (
    SELECT case_name, suite, any(file) AS file
    FROM ci.tests FINAL
    WHERE repo = 'pytorch/pytorch' AND (case_name, suite) IN (SELECT case_name, suite FROM disabled)
    GROUP BY case_name, suite
),
recent_runs AS (
    SELECT id
    FROM default.workflow_run
    WHERE created_at > now() - INTERVAL {days:UInt32} DAY
      AND repository.full_name = 'pytorch/pytorch'
),
reruns AS (
    SELECT
        name AS case_name,
        replaceRegexpOne(classname, '^.*\\.', '') AS suite,
        sum(num_green) AS green,
        sum(num_red) AS red,
        max(flaky) AS flaky
    FROM default.rerun_disabled_tests
    WHERE workflow_id IN (SELECT id FROM recent_runs)
    GROUP BY case_name, suite
),
labeled AS (
    SELECT
        d.name AS test_name,
        d.issue AS issue_number,
        arrayJoin(multiIf(f.file = '', ['unmatched'], empty(o.owners), ['unowned'], o.owners)) AS owner,
        r.case_name != '' AS rerun,
        rerun AND r.green > 0 AND r.red = 0 AND NOT r.flaky AS green_only
    FROM disabled AS d
    LEFT JOIN files AS f ON f.case_name = d.case_name AND f.suite = d.suite
    LEFT JOIN ci.owners AS o FINAL ON o.repo = 'pytorch/pytorch' AND o.file = f.file
    LEFT JOIN reruns AS r ON r.case_name = d.case_name AND r.suite = d.suite
)
SELECT
    owner,
    uniqExact(test_name) AS disabled_tests,
    uniqExactIf(test_name, rerun) AS rerun_in_window,
    uniqExactIf(test_name, green_only) AS all_green,
    groupUniqArray(5)(issue_number) AS sample_issues
FROM labeled
WHERE {owner:String} = 'all' OR owner = {owner:String}
GROUP BY owner
ORDER BY disabled_tests DESC
LIMIT 50
