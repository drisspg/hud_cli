# Project Instructions

- Run the CLI with `uv run hud ...` during development.
- Run tests with `uv run --extra dev pytest`.
- Keep commands usable through global installation with `uv tool install`.
- Prefer Typer for CLI commands and Rich for human output.
- Every data command should support a concise `--json` mode for agents.
- Prefer Grafana `gcx` as the blessed data path: use `hud gcx install` if `gcx` is missing, use `gh auth token` to mint gcx tokens from HUD, then query ClickHouse and other Grafana datasources through `gcx`.
- Do not add user-facing direct HUD data API commands; common CI/CD helpers should build on `hud gcx chq` or raw log URLs.
- Prefer one blessed path over backward-compatible fallbacks; this project is early and can make breaking changes freely while iterating.
- Cap default result sizes and require explicit windows for expensive ClickHouse helpers; avoid unbounded joins/raw row dumps and prefer aggregate queries for exploratory analysis.
- Dogfood `hud gcx` for CI/CD questions: start with `hud gcx tables`, `hud gcx describe`, `hud gcx columns`, or `hud gcx sample` when unsure; use `hud gcx chq --file query.sql` or `hud gcx chq -` for multi-line SQL, then add bounded SQL examples to the skill when they prove useful.
- Do not print HUD-minted gcx tokens, Grafana tokens, GitHub tokens, or raw credentials.
