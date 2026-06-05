from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import tomllib
from platformdirs import user_config_dir

DEFAULT_BASE_URL = "https://hud.pytorch.org/api"


@dataclass(frozen=True)
class HudConfig:
    base_url: str = DEFAULT_BASE_URL
    api_token: str | None = None
    github_token: str | None = None
    gcx_path: str | None = None


CONFIG_PATH = Path(user_config_dir("pytorch-hud", "pytorch")) / "config.toml"


def load_config(config_path: Path = CONFIG_PATH) -> HudConfig:
    file_values = _load_config_file(config_path)
    return HudConfig(
        base_url=os.environ.get("HUD_BASE_URL")
        or file_values.get("base_url")
        or DEFAULT_BASE_URL,
        api_token=os.environ.get("HUD_API_TOKEN") or file_values.get("api_token"),
        github_token=os.environ.get("GITHUB_TOKEN") or file_values.get("github_token"),
        gcx_path=os.environ.get("HUD_GCX_PATH") or file_values.get("gcx_path"),
    )


def _load_config_file(config_path: Path) -> dict[str, str]:
    if not config_path.exists():
        return {}
    data = tomllib.loads(config_path.read_text())
    hud_data = data.get("hud", {})
    if not isinstance(hud_data, dict):
        return {}
    return {key: value for key, value in hud_data.items() if isinstance(value, str)}
