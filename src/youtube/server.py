"""youtube-mcp: a read-only stdio MCP server over the YouTube Data API v3.

Run as a server (what Claude Code does):   youtube
One-time OAuth consent for your account:   youtube auth [--no-browser] [--port 8080]

Files (XDG): $XDG_CONFIG_HOME/youtube-mcp (default ~/.config/youtube-mcp), mode 700:
  api_key            API key for public reads (mode 600; restrict it to YouTube Data API v3)
  client_secret.json Desktop-app OAuth client JSON
  token.json         OAuth token, written by `youtube auth`
Cache (disposable): $XDG_CACHE_HOME/youtube-mcp/cache.sqlite (default ~/.cache/...)

Env:
  YOUTUBE_CLIENT_SECRET  override path to the OAuth client JSON
  YOUTUBE_CACHE_TTL      cache lifetime in seconds (default 3600)

Quota (default 10,000 units/day): list calls cost 1, search costs 100.
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    base = os.environ.get(var, "")
    # The XDG spec says relative values must be ignored.
    return Path(base) if base.startswith("/") else Path(default).expanduser()


CONF_DIR = _xdg("XDG_CONFIG_HOME", "~/.config") / "youtube-mcp"  # secrets: keep
CACHE_DIR = _xdg("XDG_CACHE_HOME", "~/.cache") / "youtube-mcp"  # disposable
CLIENT_SECRET = Path(
    os.environ.get("YOUTUBE_CLIENT_SECRET", CONF_DIR / "client_secret.json")
).expanduser()
API_KEY_FILE = CONF_DIR / "api_key"
TOKEN_FILE = CONF_DIR / "token.json"
CACHE_DB = CACHE_DIR / "cache.sqlite"
CACHE_TTL = int(os.environ.get("YOUTUBE_CACHE_TTL", "3600"))
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


def _ensure_dir(d: Path | None = None) -> None:
    d = d or CONF_DIR
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)


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
        raise RuntimeError("Not authorized yet. Run: youtube auth")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid:
        if creds.refresh_token:
            creds.refresh(Request())
            _write_token(creds)
        else:
            raise RuntimeError("Token invalid and not refreshable. Run: youtube auth")
    return creds


def _api_key() -> str:
    """Read the API key from CONF_DIR/api_key (no environment variable).

    The file may hold a bare key or a `YOUTUBE_API_KEY=...` line. It must not be
    readable by group/others.
    """
    if not API_KEY_FILE.exists():
        raise RuntimeError(f"No API key: create {API_KEY_FILE} (mode 600)")
    if API_KEY_FILE.stat().st_mode & 0o077:
        raise RuntimeError(f"{API_KEY_FILE} is accessible by others; run: chmod 600 {API_KEY_FILE}")
    for line in API_KEY_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if sep and name.strip() == "YOUTUBE_API_KEY":
            line = value
        elif sep:
            continue
        if key := line.strip().strip("'\""):
            return key
    raise RuntimeError(f"No YOUTUBE_API_KEY found in {API_KEY_FILE}")


_clients = {}


def _yt(oauth: bool):
    from googleapiclient.discovery import build

    if oauth not in _clients:
        if oauth:
            _clients[oauth] = build(
                "youtube", "v3", credentials=_oauth_creds(), cache_discovery=False
            )
        else:
            key = _api_key()
            _clients[oauth] = build("youtube", "v3", developerKey=key, cache_discovery=False)
    return _clients[oauth]


# -------------------------------------------------------------------------- cache
def _db() -> sqlite3.Connection:
    _ensure_dir(CACHE_DIR)
    db = sqlite3.connect(CACHE_DB)
    os.chmod(CACHE_DB, 0o600)  # cached private data (playlists, subscriptions)
    db.execute("CREATE TABLE IF NOT EXISTS c (k TEXT PRIMARY KEY, t REAL, v TEXT)")
    return db


def call(resource: str, oauth: bool, ttl: int | None = None, **params) -> dict:
    """Run resource.list(**params) with a SQLite cache. Cache is namespaced by auth mode."""
    from googleapiclient.errors import HttpError

    ttl = CACHE_TTL if ttl is None else ttl
    params = {k: v for k, v in params.items() if v is not None}
    key = json.dumps([resource, oauth, params], sort_keys=True)
    with _db() as db:
        row = db.execute("SELECT t, v FROM c WHERE k=?", (key,)).fetchone()
        if row and time.time() - row[0] < ttl:
            return json.loads(row[1])
        try:
            res = getattr(_yt(oauth), resource)().list(**params).execute()
        except HttpError as e:
            # str(HttpError) contains the full request URL, including `key=<API key>`.
            raise RuntimeError(f"YouTube API error {e.resp.status}: {e.reason}") from None
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
        playlist_id: str, max_results: int = 50, page_token: str = "",
        include_durations: bool = False,
    ) -> dict:
        """Videos in a playlist (private ones only if it is yours and authorized). (1 unit/page)
        include_durations=True adds a `duration` (ISO 8601, null if unavailable) to each item,
        at an extra +1 unit per page of up to 50 videos."""
        res = call(
            "playlistItems", True, ttl=300, part="snippet,contentDetails",
            playlistId=playlist_id, maxResults=_clamp(max_results),
            pageToken=page_token or None,
        )
        items = res.get("items", [])
        if include_durations:
            ids = [v for i in items if (v := i.get("contentDetails", {}).get("videoId"))]
            durations = {}
            for n in range(0, len(ids), 50):
                videos = call("videos", False, part="contentDetails", id=",".join(ids[n : n + 50]))
                for v in videos.get("items", []):
                    durations[v["id"]] = v.get("contentDetails", {}).get("duration")
            for i in items:
                i["duration"] = durations.get(i.get("contentDetails", {}).get("videoId"))
        return _page(res, items)

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
