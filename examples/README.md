# Examples

These examples intentionally keep `hud` as a primitive data CLI. They fetch or consume JSON from `hud`, then build reports in standalone scripts.

## Slowest test files Plotly report

With live HUD access:

```bash
uv run hud recipe slow-test-files --limit 500 --json > /tmp/slow-test-files.json
uv run --with plotly --with typer python examples/slow_test_files_plot.py /tmp/slow-test-files.json --output /tmp/slow-test-files.html --limit 50
open /tmp/slow-test-files.html
```

With sample data:

```bash
uv run --with plotly --with typer python examples/slow_test_files_plot.py examples/slow_test_files_sample.json --output docs/assets/slow-test-files-example.html --limit 10
open docs/assets/slow-test-files-example.html
```

The chart groups rows by test file, sorts by total average runtime across configs/jobs, and includes hover details for the slowest configs per file.
