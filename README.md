# youtube-mcp

Read-only stdio MCP server for the YouTube Data API v3. One file: `server.py` (deps are inline, run via `uv`).

## Setup

1. In Google Cloud: enable YouTube Data API v3, create a **Desktop app** OAuth client, add yourself as a test user, create an API key restricted to the YouTube Data API.
2. Put the client JSON at `~/.config/youtube-mcp/client_secret.json`.
3. One-time consent (read-only scope):
   ```bash
   ./server.py auth                 # opens browser
   ./server.py auth --no-browser    # headless: SSH-forward port 8080, open the printed URL locally
   ```
   Token is saved to `~/.config/youtube-mcp/token.json` (mode 600). Note: External apps in "Testing" status get refresh tokens that expire in 7 days.
4. Register with Claude Code (local scope, so the key stays in `~/.claude.json`, not in a repo):
   ```bash
   claude mcp add --transport stdio youtube --env YOUTUBE_API_KEY=... -- /home/dev/prj/youtube/server.py
   ```
   Then run `/mcp` in a session to confirm it connected.

## Tools

| Tool | Cost |
|---|---|
| `my_channel`, `my_playlists`, `my_subscriptions` (OAuth) | 1 |
| `get_channel`, `list_uploads`, `playlist_items`, `get_comments` | 1/page |
| `get_videos` | 1 per 50 ids |
| `search` | **100** |

Responses are cached in SQLite (`~/.config/youtube-mcp/cache.sqlite`, 1h default, 5 min for your own data, 24h for search).
