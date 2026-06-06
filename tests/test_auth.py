import subprocess

from hud.auth import load_config


def test_load_config_uses_gh_auth_token_for_github_token(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("hud.auth.shutil.which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(
        "hud.auth.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "github-token\n", ""),
    )

    assert load_config(tmp_path / "missing.toml").github_token == "github-token"


def test_load_config_prefers_explicit_github_token_over_gh(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "explicit-token")
    monkeypatch.setattr("hud.auth.shutil.which", lambda name: "/usr/bin/gh")

    assert load_config(tmp_path / "missing.toml").github_token == "explicit-token"
