from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from curl_cffi import requests

RAW_BASE_URL = "https://raw.githubusercontent.com/pytorch/pytorch"


def fetch_repo_files(sha: str, paths: set[str], max_workers: int = 16) -> dict[str, str | None]:
    """Fetch pytorch/pytorch files at `sha`; missing or failed files map to None."""

    def fetch(path: str) -> str | None:
        try:
            response = requests.get(f"{RAW_BASE_URL}/{sha}/{path}", impersonate="chrome", timeout=30)
        except requests.RequestsError:
            return None
        return response.text if response.status_code == 200 else None

    ordered = sorted(paths)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return dict(zip(ordered, pool.map(fetch, ordered), strict=True))
