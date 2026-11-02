-- Candidates for the @periodic decorator: slow test cases on main that ran in pull/trunk
-- and had zero failed and zero flaky runs in every main configuration over the ci.health window.
-- @periodic moves a test to periodic-strict (every 4h, failures auto-revert), so it must be stable.
-- Source: ci.health (precomputed snapshot, ran_on = 'main'); window must be a ci.health
-- window: 3h, 12h, 1d, 3d, 7d, 14d.
-- Stability is checked across all main workflows (slow, periodic, rocm, ...), not just pull/trunk.
-- Cost columns sum only (test, configuration) rows whose workflow_paths include pull.yml or
-- trunk.yml; such a row also counts any other workflows that config ran in (e.g. slow.yml).
-- Tests already seen in periodic-strict are excluded. configs = distinct (environment, TEST_CONFIG).
-- owner: ci.owners label without "module: " (e.g. nn, inductor); "all" means every owner.
-- @param window=14d
-- @param owner=all
-- @param min_mean_seconds=5
WITH per_test AS (
    SELECT
        tests_id,
        sum(failed_runs) AS failed,
        sum(flaky_runs) AS flaky,
        max(has(workflow_paths, '.github/workflows/periodic-strict.yml')) AS in_periodic_strict,
        sumIf(
            total_duration_milliseconds,
            hasAny(workflow_paths, ['.github/workflows/pull.yml', '.github/workflows/trunk.yml'])
        ) AS pull_trunk_ms,
        sumIf(runs, hasAny(workflow_paths, ['.github/workflows/pull.yml', '.github/workflows/trunk.yml'])) AS pull_trunk_runs,
        sumIf(passed_runs, hasAny(workflow_paths, ['.github/workflows/pull.yml', '.github/workflows/trunk.yml'])) AS pull_trunk_passed,
        uniqExactIf(
            (environments_id, env_vars_id),
            hasAny(workflow_paths, ['.github/workflows/pull.yml', '.github/workflows/trunk.yml'])
        ) AS configs
    FROM ci.health
    WHERE ran_on = 'main' AND window = {window:String}
    GROUP BY tests_id
    HAVING failed = 0 AND flaky = 0 AND in_periodic_strict = 0 AND pull_trunk_passed > 0
)
SELECT
    t.file AS file,
    t.suite AS suite,
    t.case_name AS case_name,
    if(empty(o.owners), ['unowned'], o.owners) AS owners,
    round(p.pull_trunk_ms / 3600000, 2) AS total_hours,
    round(p.pull_trunk_ms / p.pull_trunk_runs / 1000, 1) AS mean_seconds,
    p.pull_trunk_runs AS runs,
    p.configs AS configs
FROM per_test AS p
INNER JOIN ci.tests AS t FINAL ON t.id = p.tests_id
LEFT JOIN ci.owners AS o FINAL ON o.repo = t.repo AND o.file = t.file
WHERE t.repo = 'pytorch/pytorch'
  AND p.pull_trunk_ms / p.pull_trunk_runs >= {min_mean_seconds:Float64} * 1000
  AND ({owner:String} = 'all' OR has(o.owners, {owner:String}))
ORDER BY p.pull_trunk_ms DESC
LIMIT 50
