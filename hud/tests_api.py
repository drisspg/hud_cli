from __future__ import annotations

from typing import Any

from curl_cffi import requests

from hud.auth import HudConfig


class TestsApiError(RuntimeError):
    __test__ = False  # not a pytest test class despite the Test* name


def authed_api_url(config: HudConfig, path: str) -> str:
    """Map an OpenAPI path such as /api/tests to HUD's token-authenticated route.

    The unprefixed /api/... routes answer CLI callers with a bot challenge, so every
    request goes through /api/authed/... instead.
    """
    route = path.strip("/")
    if not route.startswith("api/"):
        raise TestsApiError(
            f"{path!r} is not an API path; use /api/... paths (HUD page paths are for linking, not fetching)"
        )
    route = route.removeprefix("api/").removeprefix("authed/")
    return f"{config.base_url.rstrip('/')}/authed/{route}"


def parse_query_params(params: list[str]) -> list[tuple[str, str]]:
    pairs = []
    for param in params:
        key, separator, value = param.partition("=")
        if not separator or not key:
            raise TestsApiError(f"query parameter must look like key=value: {param!r}")
        pairs.append((key, value))
    return pairs


def tests_api_get(
    config: HudConfig, path: str, params: list[tuple[str, str]] | None = None
) -> Any:
    if not config.github_token:
        raise TestsApiError(
            "GitHub auth is required. Run `gh auth login --hostname github.com --git-protocol ssh --web`."
        )
    try:
        response = requests.get(
            authed_api_url(config, path),
            params=params or [],
            headers={"authorization": f"Bearer {config.github_token}"},
            impersonate="chrome",
            timeout=120,
        )
    except requests.RequestsError as error:
        raise TestsApiError(f"HUD Tests API request failed: {error}") from error
    if response.status_code in {401, 403}:
        raise TestsApiError(
            "HUD refused the Tests API request. Confirm `gh auth status` and that your GitHub account has write access to pytorch/pytorch."
        )
    if response.status_code >= 400:
        raise TestsApiError(f"HUD Tests API returned HTTP {response.status_code}: {response.text[:500]}")
    try:
        return response.json()
    except ValueError as error:
        raise TestsApiError(f"HUD Tests API returned non-JSON content: {response.text[:200]}") from error


def tests_api_get_pages(
    config: HudConfig, path: str, params: list[tuple[str, str]], max_pages: int
) -> dict[str, Any]:
    """Follow pagination.next cursors, concatenating top-level list fields across pages.

    Non-list fields (counts, hud_url, timings) describe the first page.
    """
    merged = tests_api_get(config, path, params)
    if not isinstance(merged, dict):
        raise TestsApiError("--all needs an object response with pagination")
    cursor_params = [(key, value) for key, value in params if key != "cursor"]
    pages = 1
    while pages < max_pages and (cursor := next_cursor(merged)):
        page = tests_api_get(config, path, [*cursor_params, ("cursor", cursor)])
        for key, value in page.items():
            if isinstance(value, list) and isinstance(merged.get(key), list):
                merged[key].extend(value)
        merged["pagination"] = page.get("pagination")
        pages += 1
    merged["pages_fetched"] = pages
    merged["truncated"] = next_cursor(merged) is not None
    return merged


def next_cursor(response: dict[str, Any]) -> str | None:
    next_page = (response.get("pagination") or {}).get("next") or {}
    return next_page.get("cursor")


def openapi_endpoints(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "path": path,
            "kind": "page" if "pages" in operation.get("tags", []) else "api",
            "summary": operation.get("summary", ""),
        }
        for path, methods in schema["paths"].items()
        if (operation := methods.get("get"))
    ]


def openapi_parameters(schema: dict[str, Any], path: str) -> list[dict[str, Any]]:
    operation = schema["paths"].get(path, {}).get("get")
    if operation is None:
        known = ", ".join(schema["paths"])
        raise TestsApiError(f"unknown Tests API path {path!r}; known paths: {known}")
    return [
        {
            "name": parameter["name"],
            "in": parameter["in"],
            "required": parameter.get("required", False),
            "type": parameter_type(parameter.get("schema", {})),
            "description": parameter.get("description", ""),
        }
        for parameter in operation.get("parameters", [])
    ]


def parameter_type(schema: dict[str, Any]) -> str:
    if schema.get("type") == "array":
        return f"{parameter_type(schema.get('items', {}))}[]"
    if enum := schema.get("enum"):
        return "|".join(str(value) for value in enum)
    return str(schema.get("type", ""))
