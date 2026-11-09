#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mcp>=1.2,<2",
#   "google-api-python-client>=2.100",
#   "google-auth-oauthlib>=1.2",
# ]
# ///
"""youtube-mcp: a read-only stdio MCP server over the YouTube Data API v3.

Run as a server (what Claude Code does):   server.py
One-time OAuth consent for your account:   server.py auth [--no-browser] [--port 8080]

Env:
  YOUTUBE_API_KEY        API key for public reads (restrict it to YouTube Data API v3)
  YOUTUBE_CLIENT_SECRET  path to the Desktop-app OAuth client JSON
                         (default ~/.config/youtube-mcp/client_secret.json)
  YOUTUBE_MCP_DIR        config/cache dir (default ~/.config/youtube-mcp)
  YOUTUBE_CACHE_TTL      cache lifetime in seconds (default 3600)

Quota (default 10,000 units/day): list calls cost 1, search costs 100.
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

CONF_DIR = Path(os.environ.get("YOUTUBE_MCP_DIR", "~/.config/youtube-mcp")).expanduser()
CLIENT_SECRET = Path(
    os.environ.get("YOUTUBE_CLIENT_SECRET", CONF_DIR / "client_secret.json")
).expanduser()
TOKEN_FILE = CONF_DIR / "token.json"
CACHE_DB = CONF_DIR / "cache.sqlite"
CACHE_TTL = int(os.environ.get("YOUTUBE_CACHE_TTL", "3600"))
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


def _ensure_dir() -> None:
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONF_DIR, 0o700)


# --------------------------------------------------------------------------- auth
def _write_token(creds) -> None:
    _ensure_dir()
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())
    os.chmod(TOKEN_FILE, 0o600)


def run_consent(no_browser: bool, port: int) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CLIENT_SECRET.exists():
        sys.exit(f"Missing OAuth client file: {CLIENT_SECRET}")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(
        port=port, open_browser=not no_browser, access_type="offline", prompt="consent"
    )
    _write_token(creds)
    print(f"Saved token to {TOKEN_FILE} (mode 600).")


def _oauth_creds():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_FILE.exists():
        raise RuntimeError(f"Not authorized yet. Run: {sys.argv[0]} auth")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid:
        if creds.refresh_token:
            creds.refresh(Request())
            _write_token(creds)
        else:
            raise RuntimeError(f"Token invalid and not refreshable. Run: {sys.argv[0]} auth")
    return creds


_clients = {}


def _yt(oauth: bool):
    from googleapiclient.discovery import build

    if oauth not in _clients:
        if oauth:
            _clients[oauth] = build(
                "youtube", "v3", credentials=_oauth_creds(), cache_discovery=False
            )
        else:
            key = os.environ.get("YOUTUBE_API_KEY")
            if not key:
                raise RuntimeError("YOUTUBE_API_KEY is not set")
            _clients[oauth] = build("youtube", "v3", developerKey=key, cache_discovery=False)
    return _clients[oauth]


# -------------------------------------------------------------------------- cache
def _db() -> sqlite3.Connection:
    _ensure_dir()
    db = sqlite3.connect(CACHE_DB)
    db.execute("CREATE TABLE IF NOT EXISTS c (k TEXT PRIMARY KEY, t REAL, v TEXT)")
    return db


def call(resource: str, oauth: bool, ttl: int | None = None, **params) -> dict:
    """Run resource.list(**params) with a SQLite cache. Cache is namespaced by auth mode."""
    ttl = CACHE_TTL if ttl is None else ttl
    params = {k: v for k, v in params.items() if v is not None}
    key = json.dumps([resource, oauth, params], sort_keys=True)
    with _db() as db:
        row = db.execute("SELECT t, v FROM c WHERE k=?", (key,)).fetchone()
        if row and time.time() - row[0] < ttl:
            return json.loads(row[1])
        res = getattr(_yt(oauth), resource)().list(**params).execute()
        db.execute("REPLACE INTO c VALUES (?,?,?)", (key, time.time(), json.dumps(res)))
    return res


def _page(res: dict, items) -> dict:
    return {
        "items": items,
        "next_page_token": res.get("nextPageToken"),
        "total_results": res.get("pageInfo", {}).get("totalResults"),
    }


def _clamp(n: int, hi: int = 50) -> int:
    return max(1, min(n, hi))


# -------------------------------------------------------------------------- tools
def build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("youtube")

    @mcp.tool()
    def my_channel() -> dict:
        """Your own channel: id, title, stats, and uploads playlist id. (OAuth, 1 unit)"""
        res = call(
            "channels", True, ttl=300,
            part="snippet,statistics,contentDetails,brandingSettings", mine=True,
        )
        return _page(res, res.get("items", []))

    @mcp.tool()
    def get_channel(channel_id: str = "", handle: str = "") -> dict:
        """Look up any public channel by id (UC...) or @handle. (1 unit)"""
        if not (channel_id or handle):
            raise ValueError("Provide channel_id or handle")
        res = call(
            "channels", False, part="snippet,statistics,contentDetails",
            id=channel_id or None, forHandle=handle or None,
        )
        return _page(res, res.get("items", []))

    @mcp.tool()
    def list_uploads(
        channel_id: str = "", max_results: int = 25, page_token: str = ""
    ) -> dict:
        """Videos uploaded by a channel, newest first. Empty channel_id means your own
        channel. Returns video ids/titles/dates only; use get_videos for stats. (1 unit/page)"""
        if channel_id:
            ch = call("channels", False, part="contentDetails", id=channel_id)
        else:
            ch = call("channels", True, part="contentDetails", mine=True)
        if not ch.get("items"):
            raise ValueError("Channel not found")
        uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        res = call(
            "playlistItems", False, part="snippet,contentDetails", playlistId=uploads,
            maxResults=_clamp(max_results), pageToken=page_token or None,
        )
        items = [
            {
                "video_id": i["contentDetails"]["videoId"],
                "title": i["snippet"]["title"],
                "published_at": i["contentDetails"].get("videoPublishedAt"),
                "position": i["snippet"]["position"],
            }
            for i in res.get("items", [])
        ]
        return _page(res, items)

    @mcp.tool()
    def get_videos(ids: list[str]) -> dict:
        """Details and stats for video ids (batched 50 per call internally). (1 unit/50 videos)"""
        out = []
        for i in range(0, len(ids), 50):
            res = call(
                "videos", False, part="snippet,statistics,contentDetails,status",
                id=",".join(ids[i : i + 50]),
            )
            out.extend(res.get("items", []))
        return {"items": out, "count": len(out)}

    @mcp.tool()
    def get_comments(
        video_id: str, max_results: int = 50, page_token: str = "",
        order: str = "relevance",
    ) -> dict:
        """Top-level comment threads on a video (with reply counts). order: relevance|time.
        Errors if comments are disabled on the video. (1 unit/page)"""
        res = call(
            "commentThreads", False, part="snippet", videoId=video_id,
            maxResults=_clamp(max_results, 100), pageToken=page_token or None,
            order=order, textFormat="plainText",
        )
        items = []
        for t in res.get("items", []):
            s = t["snippet"]["topLevelComment"]["snippet"]
            items.append(
                {
                    "thread_id": t["id"],
                    "author": s.get("authorDisplayName"),
                    "text": s.get("textDisplay"),
                    "likes": s.get("likeCount"),
                    "published_at": s.get("publishedAt"),
                    "reply_count": t["snippet"].get("totalReplyCount"),
                }
            )
        return _page(res, items)

    @mcp.tool()
    def my_playlists(max_results: int = 50, page_token: str = "") -> dict:
        """Your playlists. (OAuth, 1 unit/page)"""
        res = call(
            "playlists", True, ttl=300, part="snippet,contentDetails,status", mine=True,
            maxResults=_clamp(max_results), pageToken=page_token or None,
        )
        return _page(res, res.get("items", []))

    @mcp.tool()
    def playlist_items(
        playlist_id: str, max_results: int = 50, page_token: str = ""
    ) -> dict:
        """Videos in a playlist (private ones only if it is yours and authorized). (1 unit/page)"""
        res = call(
            "playlistItems", True, ttl=300, part="snippet,contentDetails",
            playlistId=playlist_id, maxResults=_clamp(max_results),
            pageToken=page_token or None,
        )
        return _page(res, res.get("items", []))

    @mcp.tool()
    def my_subscriptions(max_results: int = 50, page_token: str = "") -> dict:
        """Channels you subscribe to. (OAuth, 1 unit/page)"""
        res = call(
            "subscriptions", True, ttl=300, part="snippet", mine=True,
            maxResults=_clamp(max_results), pageToken=page_token or None,
        )
        return _page(res, res.get("items", []))

    @mcp.tool()
    def search(
        q: str, type: str = "video", max_results: int = 10, page_token: str = ""
    ) -> dict:
        """EXPENSIVE: 100 quota units per call (~100 searches/day total). Prefer list_uploads
        or get_videos when you already know the channel or ids. type: video|channel|playlist."""
        res = call(
            "search", False, ttl=86400, part="snippet", q=q, type=type,
            maxResults=_clamp(max_results), pageToken=page_token or None,
        )
        return _page(res, res.get("items", []))

    return mcp


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "auth":
        port = int(args[args.index("--port") + 1]) if "--port" in args else 8080
        run_consent("--no-browser" in args, port)
    else:
        build_server().run()  # stdio


if __name__ == "__main__":
    main()
