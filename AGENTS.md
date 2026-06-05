# Project Instructions

- Run the CLI with `uv run hud ...` during development.
- Run tests with `uv run --extra dev pytest`.
- Keep commands usable through global installation with `uv tool install`.
- Prefer Typer for CLI commands and Rich for human output.
- Every data command should support a concise `--json` mode for agents.
- Keep authentication seamless: tokenless HUD access first, optional `HUD_INTERNAL_BOT_TOKEN`, optional `GITHUB_TOKEN`, and clear errors when access is blocked.
- Do not print credential values.
- Cap default result sizes and require explicit windows for expensive ClickHouse recipes.
