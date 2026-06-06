from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from curl_cffi import requests
from platformdirs import user_data_dir

from hud.auth import HudConfig

GRAFANA_SERVER = "https://pytorchci.grafana.net"
CLICKHOUSE_DATASOURCE_UID = "ceczcsck1b20wb"
GCX_VERSION = "v0.4.0"
GCX_INSTALL_DIR = Path(user_data_dir("pytorch-hud", "pytorch")) / "bin"


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
    if path := shutil.which("gcx"):
        return GcxStatus(path, "PATH", _grafana_token_set())
    managed_path = managed_gcx_path()
    if managed_path.exists():
        return GcxStatus(str(managed_path), "hud gcx install", _grafana_token_set())
    return GcxStatus(None, "missing", _grafana_token_set())


def managed_gcx_path() -> Path:
    return GCX_INSTALL_DIR / "gcx"


def install_gcx(version: str = GCX_VERSION, force: bool = False) -> Path:
    destination = managed_gcx_path()
    if destination.exists() and not force:
        return destination
    GCX_INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = GCX_INSTALL_DIR / gcx_archive_name(version)
    url = f"https://github.com/grafana/gcx/releases/download/{version}/{archive_path.name}"
    response = requests.get(url, impersonate="chrome", timeout=120)
    response.raise_for_status()
    archive_path.write_bytes(response.content)
    with tarfile.open(archive_path, "r:gz") as archive:
        member = archive.getmember("gcx")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise GcxError(f"gcx binary missing from {archive_path.name}")
        destination.write_bytes(extracted.read())
    destination.chmod(0o755)
    archive_path.unlink(missing_ok=True)
    return destination


def gcx_archive_name(version: str) -> str:
    os_name = platform.system().lower()
    machine = platform.machine().lower()
    match os_name:
        case "darwin":
            goos = "darwin"
        case "linux":
            goos = "linux"
        case _:
            raise GcxError(f"unsupported OS for managed gcx install: {platform.system()}")
    match machine:
        case "arm64" | "aarch64":
            goarch = "arm64"
        case "x86_64" | "amd64":
            goarch = "amd64"
        case _:
            raise GcxError(f"unsupported architecture for managed gcx install: {platform.machine()}")
    return f"gcx_{version.removeprefix('v')}_{goos}_{goarch}.tar.gz"


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
