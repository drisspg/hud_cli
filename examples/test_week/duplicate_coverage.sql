-- Possibly redundant coverage: test cases that ran (not skipped) in many distinct configurations on
-- main over the last {days} days, ranked by total time. A configuration is one
-- (ci.environments row, ci.env_vars row) pair: OS/arch/CPU ISA/accelerator+version/device/count/
-- Python/compiler x env vars such as TEST_CONFIG, PYTORCH_TEST_WITH_DYNAMO, PYTORCH_TEST_WITH_SLOW.
-- Counts every main-branch workflow (pull, trunk, periodic, inductor, rocm, ...), not only pull/trunk.
-- Caveats: many configs are intentional (different hardware or compile modes); look for
-- near-duplicates, e.g. many configs but few accelerators/test_configs, or the same TEST_CONFIG
-- across CPU ISAs or Python versions. Version bumps (CUDA/ROCm/compiler) create new environment
-- rows, so a rollout window can double-count one logical config.
-- @param days=1
-- @param min_configs=10
WITH main_jobs AS (
    SELECT id
    FROM default.workflow_job
    WHERE completed_at > now() - INTERVAL {days:UInt32} DAY
      AND repository_full_name = 'pytorch/pytorch'
      AND head_branch = 'main'
),
per_config AS (
    SELECT
        tests_id,
        environments_id,
        env_vars_id,
        sum(dateDiff('millisecond', started_at, ended_at)) AS ms,
        count() AS attempts
    FROM ci.runs
    WHERE started_at > now() - INTERVAL {days:UInt32} DAY
      AND github_workflow_job_id IN (SELECT id FROM main_jobs)
      AND outcome != 'skipped'
    GROUP BY tests_id, environments_id, env_vars_id
),
per_test AS (
    SELECT
        p.tests_id AS tests_id,
        count() AS configs,
        uniqExact(e.accelerator, e.device_name) AS devices,
        uniqExact(e.python_version) AS pythons,
        uniqExact(v.env_vars['TEST_CONFIG']) AS test_configs,
        arraySort(groupUniqArray(v.env_vars['TEST_CONFIG'])) AS test_config_names,
        sum(p.ms) AS ms,
        sum(p.attempts) AS attempts
    FROM per_config AS p
    LEFT JOIN ci.environments AS e FINAL ON e.id = p.environments_id
    LEFT JOIN ci.env_vars AS v FINAL ON v.id = p.env_vars_id
    GROUP BY tests_id
    HAVING configs >= {min_configs:UInt32}
)
SELECT
    t.file,
    t.suite,
    t.case_name,
    pt.configs,
    pt.devices,
    pt.pythons,
    pt.test_configs,
    pt.test_config_names,
    round(pt.ms / 3600000, 2) AS total_hours,
    round(pt.ms / pt.attempts / 1000, 2) AS mean_seconds,
    pt.attempts
FROM per_test AS pt
INNER JOIN ci.tests AS t FINAL ON t.id = pt.tests_id
WHERE t.repo = 'pytorch/pytorch'
ORDER BY pt.ms DESC
LIMIT 50
