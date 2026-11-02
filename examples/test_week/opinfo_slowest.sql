-- Slowest OpInfo ops on main over the last {days} days: total test time per op across
-- OpInfo-driven test files (file_regex), summed over devices, dtypes, configs, and templates.
-- ci.tests stores declared_case_name (the test template, e.g. test_comprehensive) beside
-- case_name = declared_case_name + '_' + op + '_' + device + optional '_' + dtype, e.g.
-- test_comprehensive_ones_like_cpu_int64 -> op ones_like. op keeps the OpInfo variant
-- (max_reduction_with_dim) and spells dots as underscores (nn_functional_conv2d, _refs_abs).
-- Caveats: file_regex must only match OpInfo files; device-generic but non-OpInfo tests in
-- those files (no op between template and device) are dropped, and ModuleInfo tests in
-- functorch/test_aotdispatch.py show up as nn_<Module> ops. Counts every main-branch workflow.
-- @param days=1
-- @param file_regex=^test/(test_ops|test_ops_gradients|test_ops_fwd_gradients|test_ops_jit|test_meta|test_decomp|test_proxy_tensor|test_schema_check|inductor/test_torchinductor_opinfo|functorch/test_ops|functorch/test_aotdispatch|distributed/tensor/test_dtensor_ops)\.py$
WITH main_jobs AS (
    SELECT id
    FROM default.workflow_job
    WHERE completed_at > now() - INTERVAL {days:UInt32} DAY
      AND repository_full_name = 'pytorch/pytorch'
      AND head_branch = 'main'
),
opinfo_tests AS (
    SELECT
        id,
        file,
        declared_case_name,
        replaceRegexpOne(
            substring(case_name, length(declared_case_name) + 2),
            '_(cpu|cuda|mps|xpu|meta)(_(float|bfloat|complex|int|uint|bool)[a-z0-9_]*)?$',
            ''
        ) AS op
    FROM ci.tests FINAL
    WHERE repo = 'pytorch/pytorch'
      AND match(file, {file_regex:String})
      AND startsWith(case_name, declared_case_name || '_')
      AND match(case_name, '_(cpu|cuda|mps|xpu|meta)(_(float|bfloat|complex|int|uint|bool)[a-z0-9_]*)?$')
),
per_test AS (
    SELECT
        tests_id,
        sum(dateDiff('millisecond', started_at, ended_at)) AS ms,
        count() AS attempts
    FROM ci.runs
    WHERE started_at > now() - INTERVAL {days:UInt32} DAY
      AND github_workflow_job_id IN (SELECT id FROM main_jobs)
      AND tests_id IN (SELECT id FROM opinfo_tests)
    GROUP BY tests_id
)
SELECT
    o.op AS op,
    round(sum(p.ms) / 3600000, 2) AS total_hours,
    sum(p.attempts) AS attempts,
    round(sum(p.ms) / sum(p.attempts) / 1000, 2) AS mean_seconds,
    uniqExact(o.file) AS files,
    uniqExact(o.declared_case_name) AS templates,
    topKWeighted(3)(o.declared_case_name, toUInt64(p.ms)) AS top_templates
FROM per_test AS p
INNER JOIN opinfo_tests AS o ON o.id = p.tests_id
WHERE o.op != ''
GROUP BY op
ORDER BY total_hours DESC
LIMIT 50
