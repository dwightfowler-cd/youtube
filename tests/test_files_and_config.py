"""Real filesystem, real permissions, real subprocess: key file, XDG paths, auth errors."""
import os
import subprocess
import sys

import pytest

from helpers import run_tools, server_env
from youtube import server

KEY = "AIzaSyTESTTESTTESTTESTTESTTESTTESTTEST1"  # only ever sent to Google as a bogus key


def write(path, text, mode=0o600):
    path.write_text(text)
    path.chmod(mode)
    return path


@pytest.mark.parametrize(
    "content",
    [
        f"{KEY}\n",
        f"YOUTUBE_API_KEY={KEY}\n",
        f'YOUTUBE_API_KEY="{KEY}"\n',
        f"YOUTUBE_API_KEY='{KEY}'\n",
        f"# comment\n\nOTHER=ignored\nYOUTUBE_API_KEY={KEY}\n",
        f"  YOUTUBE_API_KEY = {KEY}  \n",
    ],
)
def test_api_key_file_formats(tmp_path, monkeypatch, content):
    monkeypatch.setattr(server, "API_KEY_FILE", write(tmp_path / "api_key", content))
    assert server._api_key() == KEY


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666])
def test_api_key_file_refused_when_others_can_access(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(server, "API_KEY_FILE", write(tmp_path / "api_key", KEY, mode))
    with pytest.raises(RuntimeError, match="chmod 600"):
        server._api_key()


def test_api_key_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "API_KEY_FILE", tmp_path / "nope")
    with pytest.raises(RuntimeError, match="No API key"):
        server._api_key()


@pytest.mark.parametrize("content", ["", "\n\n", "# only a comment\n", "OTHER=value\n"])
def test_api_key_file_without_a_key(tmp_path, monkeypatch, content):
    monkeypatch.setattr(server, "API_KEY_FILE", write(tmp_path / "api_key", content))
    with pytest.raises(RuntimeError, match="No YOUTUBE_API_KEY"):
        server._api_key()


def test_environment_variable_is_ignored(tmp_path, monkeypatch):
    """The API key must come from the file only, even if a stale variable is exported."""
    monkeypatch.setenv("YOUTUBE_API_KEY", "AIzaFROMENVIRONMENTFROMENVIRONMENT00")
    monkeypatch.setattr(server, "API_KEY_FILE", write(tmp_path / "api_key", KEY))
    assert server._api_key() == KEY


def _paths_in_subprocess(env_overrides):
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env.update(env_overrides)
    out = subprocess.run(
        [sys.executable, "-c",
         "from youtube import server as s; print(s.CONF_DIR); print(s.CACHE_DB)"],
        env=env, capture_output=True, text=True, check=True,
    ).stdout.split()
    return out


def test_xdg_variables_are_honored(tmp_path):
    conf, cache = _paths_in_subprocess(
        {"XDG_CONFIG_HOME": str(tmp_path / "c"), "XDG_CACHE_HOME": str(tmp_path / "k")}
    )
    assert conf == str(tmp_path / "c" / "youtube-mcp")
    assert cache == str(tmp_path / "k" / "youtube-mcp" / "cache.sqlite")


def test_xdg_defaults_and_relative_values_ignored():
    home = os.path.expanduser("~")
    conf, cache = _paths_in_subprocess({"XDG_CONFIG_HOME": "relative/dir"})
    assert conf == f"{home}/.config/youtube-mcp"  # relative XDG value ignored per spec
    assert cache == f"{home}/.cache/youtube-mcp/cache.sqlite"


def test_secrets_and_cache_live_in_different_trees(tmp_path):
    conf, cache = _paths_in_subprocess(
        {"XDG_CONFIG_HOME": str(tmp_path / "c"), "XDG_CACHE_HOME": str(tmp_path / "k")}
    )
    assert not cache.startswith(conf)


@pytest.mark.parametrize("n,expected", [(-5, 1), (0, 1), (1, 1), (50, 50), (51, 50), (9999, 50)])
def test_clamp(n, expected):
    assert server._clamp(n) == expected


def test_clamp_with_higher_limit():
    assert server._clamp(500, 100) == 100


# ---- the real server process reporting real problems -------------------------------------


def test_server_without_key_reports_clear_error(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    env = server_env(tmp_path / "cache", config_home=cfg)
    (r,) = run_tools([("get_videos", {"ids": ["jNQXAC9IVRw"]})], env)
    assert r.is_error and "No API key" in r.text


def test_server_refuses_world_readable_key_file(tmp_path):
    cfg = tmp_path / "cfg" / "youtube-mcp"
    cfg.mkdir(parents=True)
    write(cfg / "api_key", KEY, 0o644)
    env = server_env(tmp_path / "cache", config_home=tmp_path / "cfg")
    (r,) = run_tools([("get_videos", {"ids": ["jNQXAC9IVRw"]})], env)
    assert r.is_error and "chmod 600" in r.text


def test_google_rejects_invalid_key_and_error_is_clean(tmp_path):
    """Real request to Google with a bogus key: real 400, reported without echoing the key."""
    cfg = tmp_path / "cfg" / "youtube-mcp"
    cfg.mkdir(parents=True)
    write(cfg / "api_key", KEY)
    env = server_env(tmp_path / "cache", config_home=tmp_path / "cfg")
    (r,) = run_tools([("get_videos", {"ids": ["jNQXAC9IVRw"]})], env)
    assert r.is_error
    assert "400" in r.text and "API key not valid" in r.text
    assert KEY not in r.text and "key=" not in r.text


def test_auth_without_client_secret_fails_clearly(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env["XDG_CONFIG_HOME"] = str(tmp_path)
    env.pop("YOUTUBE_CLIENT_SECRET", None)
    p = subprocess.run(
        [sys.executable, "-m", "youtube", "auth"], env=env, capture_output=True, text=True,
        timeout=60,
    )
    assert p.returncode != 0
    assert "Missing OAuth client file" in (p.stderr + p.stdout)
