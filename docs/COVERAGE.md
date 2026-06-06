# HUD CLI coverage audit

This maps upstream PyTorch CI data workflows to the current `hud` CLI shape. The CLI intentionally removed user-facing direct HUD data API commands; Grafana `gcx` is the blessed data entrypoint.

## Upstream workflow coverage

| Workflow | Local CLI coverage | Validation evidence |
| --- | --- | --- |
| GitHub-backed Grafana auth | `hud gcx login` mints a Grafana token from HUD using `gh auth token`, then runs `gcx login` without printing the token | `tests/test_cli.py::test_gcx_login_uses_hud_minted_token` |
| Grafana/gcx datasource access | `hud gcx doctor`; `hud gcx run -- ...` | `tests/test_cli.py::test_gcx_doctor_json_reports_missing`; passthrough tests |
| ClickHouse through Grafana | `hud gcx chq SQL --json`; datasource UID is encoded in `hud.gcx` | `tests/test_cli.py::test_gcx_chq_outputs_rows` |
| Raw S3 job log URL | `hud job JOB_ID --json`; `hud log url JOB_ID` | `tests/test_cli.py::test_job_summary_includes_direct_log_url`; `tests/test_cli.py::test_log_url` |
| Log download/search | `hud log download`; `hud log search PATTERN --path ...`; `hud log search PATTERN --job-id ...` | `tests/test_cli.py::test_log_search_local_file` |
| Log pattern extraction | `hud log patterns LOG --json` | `tests/test_cli.py::test_log_patterns` |
| Test-result extraction | `hud log tests LOG --json`; supports unittest `FAIL:`/`ERROR:` and PyTorch pytest `FAILED file.py::test` lines | `tests/test_cli.py::test_log_tests` |
| Section filtering | `hud log sections LOG --start REGEX --end REGEX --json` | `tests/test_cli.py::test_log_sections` |
| Removed direct HUD data commands | `status`, `query`, `recipe`, and `search` are absent | `tests/test_cli.py::test_removed_hud_rate_limited_commands` |
| Agent-facing guidance | `skills/hud-cli/SKILL.md`; README examples; this coverage matrix | `README.md`, `skills/hud-cli/SKILL.md`, `AGENTS.md` |

## Validation policy

Tests are mock/batch oriented. Live Grafana calls are not required for the test suite because `gcx` installation and login are local state. Live smoke checks should be run separately:

```bash
hud auth setup
hud gcx login
hud gcx doctor --json
hud gcx chq "SHOW TABLES FROM default" --json
hud gcx run -- datasources list
```

## Non-goals

- Do not expose direct HUD rate-limited data endpoints as CLI commands.
- Do not require raw ClickHouse credentials; use Grafana's ClickHouse datasource through `gcx`.
- Do not persist or print HUD-minted Grafana tokens.
