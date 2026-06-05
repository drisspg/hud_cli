from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hud.auth import HudConfig


class GcxError(RuntimeError):
    pass


@dataclass(frozen=True)
class GcxStatus:
    path: str | None
    source: str
    grafana_token_set: bool

    @property
    def available(self) -> bool:
        return self.path is not None

    @property
    def label(self) -> str:
        if self.path:
            return f"found via {self.source}: {self.path}"
        return self.source


def gcx_status(config: HudConfig) -> GcxStatus:
    if config.gcx_path:
        configured_path = Path(config.gcx_path).expanduser()
        path = str(configured_path) if configured_path.exists() else None
        source = "HUD_GCX_PATH/config" if path else f"configured missing: {configured_path}"
        return GcxStatus(path, source, _grafana_token_set())
    return GcxStatus(shutil.which("gcx"), "PATH" if shutil.which("gcx") else "missing", _grafana_token_set())


def run_gcx(config: HudConfig, args: list[str]) -> subprocess.CompletedProcess[str]:
    status = gcx_status(config)
    if not status.path:
        raise GcxError(
            "gcx is not available. Install it directly or use the PyTorch ci-infra grafana workflow with mise, then set HUD_GCX_PATH if gcx is not on PATH."
        )
    return subprocess.run(
        [status.path, *args],
        text=True,
        capture_output=True,
        check=False,
    )


def _grafana_token_set() -> bool:
    return bool(os.environ.get("GRAFANA_TOKEN"))
