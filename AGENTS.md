# Project Instructions

- Run the CLI with `uv run hud ...` during development.
- Run tests with `uv run --extra dev pytest`.
- Keep commands usable through global installation with `uv tool install`.
- Prefer Typer for CLI commands and Rich for human output.
- Every data command should support a concise `--json` mode for agents.
- Data paths: HUD's Tests API through `hud tests` (authenticated `/api/authed/` routes) for per-test questions it covers; Grafana `gcx` for everything else. HUD manages a pinned, checksum-verified `gcx` and mints its token from `gh auth token`; query ClickHouse and other Grafana datasources through it.
- `hud trace` may read a job's GitHub `test-reports` artifact through `gh api` when `ci.runs` has no test cases for it; keep other GitHub artifact access out unless asked.
- Keep `hud tests` a generic OpenAPI-driven passthrough (`api`, `schema`) rather than per-endpoint wrappers; do not add other direct HUD data API commands. Common CI/CD helpers should build on `hud gcx chq` or raw log URLs.
- Answer test questions from the ClickHouse `ci` database, not the legacy `tests` database.
- Prefer one blessed path over backward-compatible fallbacks; this project is early and can make breaking changes freely while iterating.
- Cap default result sizes and require explicit windows for expensive ClickHouse helpers; avoid unbounded joins/raw row dumps and prefer aggregate queries for exploratory analysis.
- Dogfood `hud gcx` for CI/CD questions: start with `hud gcx tables`, `hud gcx describe`, `hud gcx columns`, or `hud gcx sample` when unsure; use `hud gcx chq --file query.sql` or `hud gcx chq -` for multi-line SQL, then add bounded SQL examples to the skill when they prove useful.
- Do not print HUD-minted gcx tokens, Grafana tokens, GitHub tokens, or raw credentials.
