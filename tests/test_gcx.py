from pathlib import Path

from hud.auth import HudConfig
from hud.gcx import gcx_archive_name, gcx_status


def test_gcx_archive_name(monkeypatch) -> None:
    monkeypatch.setattr("hud.gcx.platform.system", lambda: "Darwin")
    monkeypatch.setattr("hud.gcx.platform.machine", lambda: "arm64")

    assert gcx_archive_name("v0.4.0") == "gcx_0.4.0_darwin_arm64.tar.gz"


def test_gcx_status_finds_managed_path(monkeypatch, tmp_path: Path) -> None:
    managed = tmp_path / "gcx"
    managed.write_text("#!/bin/sh\n")
    monkeypatch.setattr("hud.gcx.shutil.which", lambda name: None)
    monkeypatch.setattr("hud.gcx.managed_gcx_path", lambda: managed)

    status = gcx_status(HudConfig())

    assert status.path == str(managed)
    assert status.source == "hud gcx install"
