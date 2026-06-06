# Examples

These examples intentionally keep `hud` as a primitive data CLI. Fetch bounded JSON with `hud gcx chq`, then build reports in standalone scripts.

## Slowest test files Plotly report with test counts

Fetch slow test-file data through Grafana ClickHouse, including the average number of tests reported for each file/config row, then plot it:

```bash
uv run hud gcx chq --file examples/slow_test_files_with_counts.sql --json > /tmp/slow-test-files-with-counts.json
uv run --with plotly --with typer python examples/slow_test_files_plot.py /tmp/slow-test-files-with-counts.json --output /tmp/slow-test-files-with-counts.html --limit 50
open /tmp/slow-test-files-with-counts.html
```

With sample data:

```bash
uv run --with plotly --with typer python examples/slow_test_files_plot.py examples/slow_test_files_sample.json --output docs/assets/slow-test-files-example.html --limit 10
open docs/assets/slow-test-files-example.html
```

The chart groups rows by test file, sorts by total average runtime across configs/jobs, colors bars by max test count for that file, and includes hover details for runtime, test counts, and the slowest configs per file.
