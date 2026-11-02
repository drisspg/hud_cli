import pytest

from hud.auth import HudConfig
from hud.tests_api import TestsApiError, authed_api_url


@pytest.mark.parametrize(
    "path",
    ["/api/tests/test/1", "api/tests/test/1", "/api/authed/tests/test/1"],
)
def test_authed_api_url_routes_through_authed_prefix(path: str) -> None:
    assert authed_api_url(HudConfig(), path) == "https://hud.pytorch.org/api/authed/tests/test/1"


def test_authed_api_url_rejects_hud_page_paths() -> None:
    with pytest.raises(TestsApiError, match="not an API path"):
        authed_api_url(HudConfig(), "/tests/pytorch/pytorch")
