from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from curl_cffi import requests

from hud.auth import HudConfig

GRAFANA_SERVER = "https://pytorchci.grafana.net"
CLICKHOUSE_DATASOURCE_UID = "ceczcsck1b20wb"


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


def login_with_hud_token(config: HudConfig, token_name: str) -> subprocess.CompletedProcess[str]:
    return run_gcx(
        config,
        [
            "login",
            "--yes",
            "pytorchci",
            "--server",
            GRAFANA_SERVER,
            "--token",
            mint_gcx_token(config, token_name),
        ],
    )


def mint_gcx_token(config: HudConfig, token_name: str) -> str:
    if not config.github_token:
        raise GcxError("GitHub auth is required. Run `gh auth login --hostname github.com --git-protocol ssh --web`.")
    url = f"{config.base_url.rstrip('/')}/gcx-token?token_name={quote(token_name)}"
    response = requests.get(
        url,
        headers={"authorization": f"Bearer {config.github_token}"},
        impersonate="chrome",
        timeout=30,
    )
    if response.status_code in {401, 403}:
        raise GcxError("HUD refused the gcx token request. Confirm your GitHub account has PyTorch HUD access.")
    response.raise_for_status()
    token = response.text.strip().strip('"')
    if not token:
        raise GcxError("HUD returned an empty gcx token")
    return token


def clickhouse_query(config: HudConfig, sql: str) -> dict[str, Any]:
    payload = {
        "queries": [
            {
                "refId": "A",
                "datasource": {
                    "type": "grafana-clickhouse-datasource",
                    "uid": CLICKHOUSE_DATASOURCE_UID,
                },
                "rawSql": sql,
                "format": 1,
                "queryType": "table",
            }
        ]
    }
    result = run_gcx(
        config,
        ["api", "/api/ds/query", "-o", "json", "-d", json.dumps(payload)],
    )
    if result.returncode != 0:
        raise GcxError(result.stderr.strip() or result.stdout.strip() or "gcx ClickHouse query failed")
    return json.loads(result.stdout)


def rows_from_gcx_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    frame = data["results"]["A"]["frames"][0]
    columns = [field["name"] for field in frame["schema"]["fields"]]
    values = frame["data"].get("values", [])
    if not values:
        return []
    return [dict(zip(columns, row, strict=False)) for row in zip(*values, strict=False)]


def run_gcx(config: HudConfig, args: list[str]) -> subprocess.CompletedProcess[str]:
    status = gcx_status(config)
    if not status.path:
        raise GcxError(
            "gcx is not available. Install gcx v0.2.16+ or use the PyTorch ci-infra grafana workflow with mise, then set HUD_GCX_PATH if gcx is not on PATH."
        )
    env = {**os.environ, "GRAFANA_SERVER": GRAFANA_SERVER}
    return subprocess.run(
        [status.path, *args],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def _grafana_token_set() -> bool:
    return bool(os.environ.get("GRAFANA_TOKEN"))
