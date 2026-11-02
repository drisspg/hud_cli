from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from curl_cffi import requests
from platformdirs import user_config_dir, user_data_dir

from hud.auth import HudConfig

GRAFANA_SERVER = "https://pytorchci.grafana.net"
CLICKHOUSE_DATASOURCE_UID = "ceczcsck1b20wb"
GCX_VERSION = "v1.7.1"
GCX_INSTALL_DIR = Path(user_data_dir("hud-cli", "pytorch")) / "bin"
# HUD owns a dedicated gcx config so it never migrates or clobbers the user's own gcx contexts.
GCX_CONFIG_PATH = Path(user_config_dir("hud-cli", "pytorch")) / "gcx.yaml"
READ_ONLY_CLICKHOUSE_COMMANDS = {"DESC", "DESCRIBE", "EXPLAIN", "SELECT", "SHOW", "WITH"}
BLOCKED_CLICKHOUSE_KEYWORDS = {
    "ALTER",
    "ATTACH",
    "BACKUP",
    "CREATE",
    "DELETE",
    "DETACH",
    "DROP",
    "GRANT",
    "INSERT",
    "KILL",
    "OPTIMIZE",
    "OUTFILE",
    "RENAME",
    "REPLACE",
    "RESTORE",
    "REVOKE",
    "SET",
    "TRUNCATE",
    "UPDATE",
    "USE",
    "WATCH",
}
SQL_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


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
    """Resolve gcx: an explicit GCX_PATH override, else HUD's pinned managed binary.

    PATH is deliberately ignored so every user runs the gcx version HUD is tested with.
    """
    if config.gcx_path:
        configured_path = Path(config.gcx_path).expanduser()
        path = str(configured_path) if configured_path.exists() else None
        source = "GCX_PATH/config" if path else f"configured missing: {configured_path}"
        return GcxStatus(path, source, _grafana_token_set())
    managed_path = managed_gcx_path()
    if managed_path.exists():
        return GcxStatus(str(managed_path), f"managed {GCX_VERSION}", _grafana_token_set())
    return GcxStatus(None, f"managed {GCX_VERSION} not installed yet", _grafana_token_set())


def managed_gcx_path(version: str = GCX_VERSION) -> Path:
    return GCX_INSTALL_DIR / f"gcx-{version}"


def install_gcx(version: str = GCX_VERSION, force: bool = False) -> Path:
    destination = managed_gcx_path(version)
    if destination.exists() and not force:
        return destination
    GCX_INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    archive_name = gcx_archive_name(version)
    release_url = f"https://github.com/grafana/gcx/releases/download/{version}"
    archive = download_release_asset(f"{release_url}/{archive_name}")
    checksums = download_release_asset(
        f"{release_url}/gcx_{version.removeprefix('v')}_checksums.txt"
    ).decode()
    verify_sha256(archive, archive_name, checksums)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        extracted = tar.extractfile("gcx")
        if extracted is None:
            raise GcxError(f"gcx binary missing from {archive_name}")
        binary = extracted.read()
    # Publish atomically so concurrent first-use installs never run a partial binary.
    with tempfile.NamedTemporaryFile(dir=GCX_INSTALL_DIR, prefix=".gcx-", delete=False) as staging:
        staging.write(binary)
    staging_path = Path(staging.name)
    staging_path.chmod(0o755)
    os.replace(staging_path, destination)
    return destination


def download_release_asset(url: str) -> bytes:
    try:
        response = requests.get(url, impersonate="chrome", timeout=120)
        response.raise_for_status()
    except requests.RequestsError as error:
        raise GcxError(f"failed to download {url}: {error}") from error
    return response.content


def verify_sha256(data: bytes, file_name: str, checksums: str) -> None:
    expected = {
        name.strip(): digest
        for digest, _, name in (line.partition(" ") for line in checksums.splitlines())
    }.get(file_name)
    if expected is None:
        raise GcxError(f"gcx checksums file has no entry for {file_name}")
    if hashlib.sha256(data).hexdigest() != expected:
        raise GcxError(f"gcx checksum mismatch for {file_name}; refusing to install")


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
    # Pass the token through the environment so it never appears in process arguments.
    return run_gcx(
        config,
        ["login", "pytorchci", "--server", GRAFANA_SERVER, "--yes"],
        extra_env={"GRAFANA_TOKEN": mint_gcx_token(config, token_name)},
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
    validate_read_only_clickhouse_sql(sql)
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
        raise GcxError(gcx_failure_message(result, "gcx ClickHouse query failed"))
    return json.loads(result.stdout)


def gcx_failure_message(result: subprocess.CompletedProcess[str], fallback: str) -> str:
    output = result.stderr.strip() or result.stdout.strip()
    if "HTTP 401" in output or "Unauthorized" in output:
        return (
            "Grafana returned 401 Unauthorized for the gcx pytorchci context. "
            "Refresh it with `hud gcx login`; this uses `gh auth token` to mint a HUD Grafana token and runs "
            f"`gcx login pytorchci --server {GRAFANA_SERVER} --yes` with the token in GRAFANA_TOKEN, never printing it. "
            "If login still fails, check `gh auth status` and PyTorch HUD access."
        )
    if "HTTP 403" in output or "Forbidden" in output:
        return (
            "Grafana returned 403 Forbidden for the gcx pytorchci context. "
            "Run `hud gcx login` to refresh the HUD-minted Grafana token; if it persists, confirm your GitHub account has PyTorch HUD access."
        )
    return output or fallback


def validate_read_only_clickhouse_sql(sql: str) -> None:
    tokens, has_statement_separator = clickhouse_sql_tokens(sql)
    if not tokens:
        raise GcxError("ClickHouse SQL cannot be empty")
    if has_statement_separator:
        raise GcxError("ClickHouse SQL must be a single read-only statement")
    if tokens[0] not in READ_ONLY_CLICKHOUSE_COMMANDS:
        raise GcxError("ClickHouse SQL must start with SELECT, WITH, SHOW, DESCRIBE, or EXPLAIN")
    if blocked := sorted(set(tokens) & BLOCKED_CLICKHOUSE_KEYWORDS):
        raise GcxError(f"ClickHouse SQL contains blocked write/admin keyword: {', '.join(blocked)}")


def clickhouse_sql_tokens(sql: str) -> tuple[list[str], bool]:
    tokens = []
    has_statement_separator = False
    after_statement_end = False
    index = 0
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""
        if char in {"'", '"', "`"}:
            index = skip_quoted_sql(sql, index, char)
            continue
        if char == "-" and next_char == "-":
            index = skip_line_comment(sql, index + 2)
            continue
        if char == "#":
            index = skip_line_comment(sql, index + 1)
            continue
        if char == "/" and next_char == "*":
            index = skip_block_comment(sql, index + 2)
            continue
        if char == ";":
            after_statement_end = bool(tokens)
            index += 1
            continue
        match = SQL_TOKEN_RE.match(sql, index)
        if match:
            has_statement_separator = has_statement_separator or after_statement_end
            tokens.append(match.group(0).upper())
            index = match.end()
            continue
        index += 1
    return tokens, has_statement_separator


def skip_quoted_sql(sql: str, index: int, quote_char: str) -> int:
    index += 1
    while index < len(sql):
        if sql[index] == "\\":
            index += 2
            continue
        if sql[index] == quote_char:
            if quote_char in {"'", '"'} and index + 1 < len(sql) and sql[index + 1] == quote_char:
                index += 2
                continue
            return index + 1
        index += 1
    return index


def skip_line_comment(sql: str, index: int) -> int:
    while index < len(sql) and sql[index] not in {"\n", "\r"}:
        index += 1
    return index


def skip_block_comment(sql: str, index: int) -> int:
    while index + 1 < len(sql):
        if sql[index] == "*" and sql[index + 1] == "/":
            return index + 2
        index += 1
    return index


def rows_from_gcx_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    frame = data["results"]["A"]["frames"][0]
    columns = [field["name"] for field in frame["schema"]["fields"]]
    values = frame["data"].get("values", [])
    if not values:
        return []
    return [dict(zip(columns, row, strict=False)) for row in zip(*values, strict=False)]


def run_gcx(
    config: HudConfig, args: list[str], extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    validate_gcx_passthrough_args(args)
    return subprocess.run(
        [ensure_gcx(config), *args],
        text=True,
        capture_output=True,
        check=False,
        env=gcx_subprocess_env(extra_env),
    )


def ensure_gcx(config: HudConfig) -> str:
    status = gcx_status(config)
    if status.path:
        return status.path
    if config.gcx_path:
        raise GcxError(f"gcx is not available: {status.source}. Fix GCX_PATH or unset it to use HUD's managed gcx.")
    return str(install_gcx())


def gcx_subprocess_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ) | (extra_env or {})
    # gcx 1.x refuses to save credentials without an OS keychain on headless hosts;
    # the HUD-minted token is a read-only Viewer token, so keep it in gcx's 0600 config.
    env.setdefault("GCX_KEYCHAIN", "off")
    env["GCX_CONFIG"] = str(GCX_CONFIG_PATH)
    if env.get("GRAFANA_TOKEN"):
        env.setdefault("GRAFANA_SERVER", GRAFANA_SERVER)
    else:
        env.pop("GRAFANA_SERVER", None)
    return env


def validate_gcx_passthrough_args(args: list[str]) -> None:
    if args[:3] == ["datasources", "clickhouse", "query"]:
        raise GcxError(
            "Run ClickHouse SQL with `hud gcx chq`, which enforces read-only SQL and parses rows."
        )
    if len(args) < 2 or args[:2] != ["api", "/api/ds/query"]:
        return
    payload = gcx_api_payload(args)
    if payload is None:
        return
    queries = payload.get("queries", [])
    if not isinstance(queries, list):
        return
    for query in queries:
        if not isinstance(query, dict):
            continue
        datasource = query.get("datasource", {})
        raw_sql = query.get("rawSql")
        if (
            isinstance(datasource, dict)
            and isinstance(raw_sql, str)
            and is_clickhouse_datasource(datasource)
        ):
            validate_read_only_clickhouse_sql(raw_sql)


def gcx_api_payload(args: list[str]) -> dict[str, Any] | None:
    for index, arg in enumerate(args):
        if arg in {"-d", "--data"} and index + 1 < len(args):
            try:
                data = json.loads(args[index + 1])
            except json.JSONDecodeError as error:
                raise GcxError(
                    "gcx /api/ds/query payload must be JSON so HUD can enforce read-only ClickHouse SQL"
                ) from error
            if isinstance(data, dict):
                return data
    return None


def is_clickhouse_datasource(datasource: dict[str, Any]) -> bool:
    return (
        datasource.get("uid") == CLICKHOUSE_DATASOURCE_UID
        or datasource.get("type") == "grafana-clickhouse-datasource"
    )


def _grafana_token_set() -> bool:
    return bool(os.environ.get("GRAFANA_TOKEN"))
