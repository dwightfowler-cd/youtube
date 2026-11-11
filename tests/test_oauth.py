"""OAuth: real token files, real Google refresh, real account data.

Tests marked `oauth` need a token from `uv run youtube auth`. Without one they FAIL
with a message saying so; they are never skipped. The browser consent flow itself cannot be automated
and is verified by running `youtube auth` by hand once.
"""
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from helpers import run_tools, server_env

ZOO = "jNQXAC9IVRw"  # "Me at the zoo", the first YouTube video (2005)
ZOO_UPLOADS = "UU4QobU6STFB0P71PMvOGN5A"  # the zoo uploader's public uploads playlist


def _env_with_config(tmp_path, config_home):
    return server_env(tmp_path / "cache", config_home=config_home)


# ---- no Google account needed ------------------------------------------------------------


def test_oauth_tools_without_a_token_tell_you_to_authenticate(tmp_path):
    empty = tmp_path / "cfg"
    empty.mkdir()
    env = _env_with_config(tmp_path, empty)
    results = run_tools(
        [("my_channel", {}), ("my_playlists", {}), ("my_subscriptions", {}),
         ("playlist_items", {"playlist_id": "PLxxxx"}), ("list_uploads", {})],
        env,
    )
    for r in results:
        assert r.is_error and "youtube auth" in r.text, r.text


def test_token_file_is_written_mode_600_and_round_trips(tmp_path):
    """Real google Credentials object -> real file -> real reload, in a real process."""
    code = """
from google.oauth2.credentials import Credentials
from youtube import server
c = Credentials(token="t", refresh_token="r", client_id="id", client_secret="s",
                token_uri="https://oauth2.googleapis.com/token", scopes=server.SCOPES)
server._write_token(c)
print(server.TOKEN_FILE)
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env["XDG_CONFIG_HOME"] = str(tmp_path)
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                         text=True, check=True).stdout.strip()
    token = tmp_path / "youtube-mcp" / "token.json"
    assert out == str(token)
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert stat.S_IMODE(token.parent.stat().st_mode) == 0o700
    data = json.loads(token.read_text())
    assert data["refresh_token"] == "r" and data["client_id"] == "id"

    from google.oauth2.credentials import Credentials

    from youtube import server

    again = Credentials.from_authorized_user_file(str(token), server.SCOPES)
    assert again.refresh_token == "r" and again.token == "t"


def test_token_file_mode_is_tightened_even_if_it_pre_exists_loose(tmp_path):
    cfg = tmp_path / "youtube-mcp"
    cfg.mkdir()
    tok = cfg / "token.json"
    tok.write_text("{}")
    tok.chmod(0o644)
    code = """
from google.oauth2.credentials import Credentials
from youtube import server
server._write_token(Credentials(token="t"))
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env["XDG_CONFIG_HOME"] = str(tmp_path)
    subprocess.run([sys.executable, "-c", code], env=env, check=True)
    assert stat.S_IMODE(tok.stat().st_mode) == 0o600


def test_requested_scope_is_read_only():
    from youtube import server

    assert server.SCOPES == ["https://www.googleapis.com/auth/youtube.readonly"]


# ---- needs a real token ------------------------------------------------------------------


@pytest.mark.oauth
def test_my_channel_returns_exactly_my_channel(tmp_path, real_token_path, real_key):
    env = server_env(tmp_path / "cache")
    d = run_tools([("my_channel", {})], env)[0].data
    assert len(d["items"]) == 1
    assert d["items"][0]["id"].startswith("UC")
    assert d["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"].startswith("UU")


@pytest.mark.oauth
def test_list_uploads_without_channel_uses_my_channel(tmp_path, real_token_path, real_key):
    env = server_env(tmp_path / "cache")
    mine, uploads = run_tools([("my_channel", {}), ("list_uploads", {"max_results": 5})], env)
    assert not uploads.is_error, uploads.text
    n = int(mine.data["items"][0]["statistics"]["videoCount"])
    assert len(uploads.data["items"]) == min(n, 5)


@pytest.mark.oauth
def test_my_playlists_returns_a_well_formed_list(tmp_path, real_token_path, real_key):
    env = server_env(tmp_path / "cache")
    (pl,) = run_tools([("my_playlists", {"max_results": 5})], env)
    assert not pl.is_error, pl.text
    # An account with no playlists legitimately returns [], so this checks the shape only.
    assert isinstance(pl.data["items"], list)
    assert len(pl.data["items"]) <= 5


@pytest.mark.oauth
def test_playlist_items_of_a_public_playlist(tmp_path, real_token_path, real_key):
    """Public, permanent reference data, so it never depends on what the account owns."""
    env = server_env(tmp_path / "cache")
    (items,) = run_tools([("playlist_items", {"playlist_id": ZOO_UPLOADS, "max_results": 5})], env)
    assert not items.is_error, items.text
    ids = [i["contentDetails"]["videoId"] for i in items.data["items"]]
    assert ZOO in ids


@pytest.mark.oauth
def test_my_subscriptions(tmp_path, real_token_path, real_key):
    env = server_env(tmp_path / "cache")
    d = run_tools([("my_subscriptions", {"max_results": 5})], env)[0].data
    assert isinstance(d["items"], list)
    for s in d["items"]:
        assert s["snippet"]["resourceId"]["channelId"].startswith("UC")


@pytest.mark.oauth
def test_expired_access_token_is_refreshed_by_google_and_saved(
    tmp_path, real_token_path, real_key
):
    """Copy the real token, mark it expired, and let the real server refresh it against Google."""
    cfg = tmp_path / "cfg" / "youtube-mcp"
    cfg.mkdir(parents=True)
    cfg.chmod(0o700)
    data = json.loads(real_token_path.read_text())
    assert data.get("refresh_token"), "real token has no refresh_token; re-run `youtube auth`"
    data["token"] = "expired-access-token"
    data["expiry"] = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    token = cfg / "token.json"
    token.write_text(json.dumps(data))
    token.chmod(0o600)
    # the real API key lives in the real config dir; a copy is needed since XDG_CONFIG_HOME moves
    key = cfg / "api_key"
    key.write_text((real_token_path.parent / "api_key").read_text())
    key.chmod(0o600)

    env = server_env(tmp_path / "cache", config_home=tmp_path / "cfg")
    r = run_tools([("my_channel", {})], env)[0]
    assert not r.is_error, r.text
    assert r.data["items"]

    saved = json.loads(token.read_text())
    assert saved["token"] != "expired-access-token", "token was not refreshed and saved"
    assert saved["refresh_token"] == data["refresh_token"]
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
