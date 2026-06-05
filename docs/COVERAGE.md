# HUD CLI coverage audit

This maps the upstream workflows from `claude-pytorch-treehugger`, `clickhouse-mcp`, Nikita's tokenless HUD gist, and PyTorch `ci-infra/grafana` gcx guidance to local `hud` CLI commands, tests, and docs.

## Upstream workflow coverage

| Upstream workflow | Local CLI coverage | Validation evidence |
| --- | --- | --- |
| Tokenless HUD access from Nikita's gist using `curl_cffi` browser impersonation | `HudClient._request(... impersonate="chrome")`; `HUD_INTERNAL_BOT_TOKEN` remains optional | `tests/test_client.py::test_clickhouse_query_encodes_parameters` asserts browser impersonation; `README.md` auth section documents tokenless default |
| HUD commit grid: `/api/hud/{owner}/{repo}/{branch_or_sha}/{page}` | `hud status BRANCH --repo OWNER/REPO`; raw `--json`; normalized `--compact-json`; status/name/failure filters | `tests/test_cli.py::test_status_compact_json_filters_failures`; CLI help smoke via `uv run hud status --help` |
| HUD job artifacts | `hud job JOB_ID --artifacts --json`; `HudClient.artifacts()` accepts top-level list responses | `tests/test_client.py::test_artifacts_accepts_top_level_list`; `README.md` job example |
| Raw S3 job log URL | `hud job JOB_ID --json`; `hud log url JOB_ID` | `tests/test_cli.py::test_job_summary_includes_direct_log_url`; `tests/test_cli.py::test_log_url` |
| Log download/search | `hud log download`; `hud log search PATTERN --path ...`; `hud log search PATTERN --job-id ...` | `tests/test_cli.py::test_log_search_local_file`; README/skill log examples |
| Log pattern extraction | `hud log patterns LOG --json` | `tests/test_cli.py::test_log_patterns` |
| Test-result extraction | `hud log tests LOG --json`; supports unittest `FAIL:`/`ERROR:` and PyTorch pytest `FAILED file.py::test` lines | `tests/test_cli.py::test_log_tests` |
| Section filtering | `hud log sections LOG --start REGEX --end REGEX --json` | `tests/test_cli.py::test_log_sections` |
| Historical similar-failure search/OpenSearch | `hud search failures TEXT --repo ... --workflow-name ... --branch-name ... --days ... --json` | `tests/test_cli.py::test_search_failures_uses_bounded_search`; `tests/test_client.py::test_similar_failures_builds_search_params` |
| Named HUD ClickHouse query execution | `hud query run QUERY -p key=value --json` | `tests/test_client.py::test_clickhouse_query_encodes_parameters`; README query examples |
| ClickHouse query catalog/metadata | `hud query list`; `hud query explain QUERY --json`; `hud query source QUERY --json/--text` | `tests/test_cli.py::test_query_list`; `tests/test_cli.py::test_query_explain_json`; `tests/test_cli.py::test_query_source` |
| Queued jobs | `hud recipe queued --json`; raw `hud query run queued_jobs` | `tests/test_cli.py::test_recipe_batch_uses_named_queries` |
| Trunk red / master commit red | `hud recipe trunk-red --days N --json`; raw `hud query run master_commit_red ...` | `tests/test_cli.py::test_recipe_batch_uses_named_queries` |
| Flaky tests | `hud recipe flaky-test [TEST] --days N --json`; raw `hud query run flaky_tests/across_jobs ...` | `tests/test_cli.py::test_recipe_batch_uses_named_queries` |
| Disabled tests | `hud recipe disabled-tests --days N --json` | `tests/test_cli.py::test_recipe_batch_uses_named_queries` |
| Time-to-signal | `hud recipe tts --days N --json`; `hud recipe tts --percentile --days N --json` | `tests/test_cli.py::test_recipe_batch_uses_named_queries` |
| Slowest test files | `hud recipe slow-test-files --limit N --json`; agents can pipe JSON into plotting/report scripts | `tests/test_cli.py::test_recipe_batch_uses_named_queries` |
| Grafana/gcx datasource access | `hud gcx doctor`; `hud gcx run -- ...`; auth doctor reports `GRAFANA_TOKEN` without printing it | `tests/test_cli.py::test_gcx_doctor_json_reports_missing`; `tests/test_cli.py::test_gcx_run_passthrough`; `tests/test_cli.py::test_gcx_run_reports_missing` |
| Agent-facing guidance | `skills/hud-cli/SKILL.md`; README examples; this coverage matrix | `README.md`, `skills/hud-cli/SKILL.md`, `AGENTS.md` |

## Validation policy

Tests are intentionally mock/batch oriented. Live HUD and Grafana calls are not required for the test suite because:

- unauthenticated HUD can be rate-limited by Vercel/HUD,
- `gcx` requires local installation plus a read-only `GRAFANA_TOKEN`,
- the goal is to verify CLI behavior, parameter construction, parsing, safety, and agent ergonomics without requiring secrets.

Live smoke checks should be run separately in an environment with HUD access and/or `gcx` configured:

```bash
hud auth doctor
hud status main --per-page 1 --compact-json
hud query run queued_jobs --json
hud gcx doctor --json
hud gcx run -- datasources list
```

## Remaining non-goals

- Direct raw ClickHouse credentials are not embedded in `hud`; use HUD named queries or `gcx` with read-only Grafana access.
- `hud` is a CLI-first replacement for practical MCP workflows, not a full MCP server implementation.
- A stable HUD job metadata endpoint beyond artifacts/direct logs has not been proven; `hud job` therefore avoids requiring one.
