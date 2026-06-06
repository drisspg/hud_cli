# Examples

These examples intentionally keep `hud` as a primitive data CLI. Fetch bounded JSON with `hud gcx chq`, then build reports in standalone scripts.

## Slowest test files Plotly report

Fetch slow test-file data through Grafana ClickHouse, then plot it:

```bash
uv run hud gcx chq "SELECT test_run.invoking_file AS file, sum(time) AS time, regexpExtract(job.name, '^(.*) /', 1) AS base_name, regexpExtract(job.name, '/ test \\(([\\w-]*),', 1) AS test_config FROM default.test_run_summary test_run INNER JOIN default.workflow_job job ON test_run.job_id = job.id WHERE test_run.file != '' AND job.completed_at > now() - INTERVAL 3 DAY GROUP BY file, base_name, test_config ORDER BY time DESC LIMIT 500" --json > /tmp/slow-test-files.json
uv run --with plotly --with typer python examples/slow_test_files_plot.py /tmp/slow-test-files.json --output /tmp/slow-test-files.html --limit 50
open /tmp/slow-test-files.html
```

With sample data:

```bash
uv run --with plotly --with typer python examples/slow_test_files_plot.py examples/slow_test_files_sample.json --output docs/assets/slow-test-files-example.html --limit 10
open docs/assets/slow-test-files-example.html
```

The chart groups rows by test file, sorts by total average runtime across configs/jobs, and includes hover details for the slowest configs per file.
