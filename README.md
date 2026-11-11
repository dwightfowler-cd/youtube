# youtube-mcp

Read-only stdio MCP server for the YouTube Data API v3. A `uv` package; the server lives in `src/youtube/server.py` and is exposed as the `youtube` command.

## Setup

0. `uv sync` (once, from the repo root).
1. In Google Cloud: enable YouTube Data API v3, create a **Desktop app** OAuth client, add yourself as a test user, create an API key restricted to the YouTube Data API.
2. Put files in `~/.config/youtube-mcp/` (mode 700):
   - `client_secret.json`: the Desktop-app OAuth client JSON.
   - `api_key` (mode 600): just the API key, nothing else (a trailing newline is fine). The server reads only this file (there is no environment-variable path) and refuses to read it if group/others can access it.

   Paths follow XDG: `$XDG_CONFIG_HOME/youtube-mcp` (default `~/.config/youtube-mcp`).
3. One-time consent (read-only scope):
   ```bash
   uv run youtube auth                 # opens browser
   uv run youtube auth --no-browser    # headless: SSH-forward port 8080, open the printed URL locally
   ```
   Token is saved to `~/.config/youtube-mcp/token.json` (mode 600). Note: External apps in "Testing" status get refresh tokens that expire in 7 days.
4. Register with Claude Code. The key is not passed on the command line; the server reads it from the key file:
   ```bash
   claude mcp add --transport stdio youtube -- uv run --project /home/dev/prj/youtube youtube
   ```
   Then run `/mcp` in a session to confirm it connected.

   **No `claude` CLI? Use a `.mcp.json` file instead.** `claude mcp add` only writes a config entry, so you can create the equivalent by hand:
   - **Where:** `.mcp.json` in the project root, next to `pyproject.toml` (here: `/home/dev/prj/youtube/.mcp.json`). Claude Code reads it for sessions opened in that directory.
   - **Contents:**
     ```json
     {
       "mcpServers": {
         "youtube": {
           "type": "stdio",
           "command": "/home/dev/.local/bin/uv",
           "args": ["run", "--project", "/home/dev/prj/youtube", "youtube"]
         }
       }
     }
     ```
   - **Why the full path to `uv`:** a desktop app may start with a minimal `PATH`, so a bare `uv` can fail with "command not found". Find yours with `which uv`.
   - **No secrets in it:** the API key is read from `~/.config/youtube-mcp/api_key`, so this file is safe to commit.
   - **Approval:** project-scoped servers ask for your approval the first time a session starts. Start a new session (it is not picked up by one already running), approve it, then run `/mcp` to confirm it connected.
   - Paths are absolute, so adjust them if the repo or `uv` lives elsewhere.
5. Claude Desktop (chat tab): add to `claude_desktop_config.json` and fully restart the app:
   ```json
   { "mcpServers": { "youtube": {
       "command": "/home/dev/.local/bin/uv",
       "args": ["run", "--project", "/home/dev/prj/youtube", "youtube"] } } }
   ```

## Tools

| Tool | Cost |
|---|---|
| `my_channel`, `my_playlists`, `my_subscriptions` (OAuth) | 1 |
| `get_channel`, `list_uploads`, `get_comments` | 1/page |
| `playlist_items` | 1/page, +1/page with `include_durations` |
| `get_videos` | 1 per 50 ids |
| `search` | **100** |

Responses are cached in SQLite at `$XDG_CACHE_HOME/youtube-mcp/cache.sqlite` (default `~/.cache/youtube-mcp/`; 1h default, 5 min for your own data, 24h for search). It is disposable: delete it any time and the only cost is re-spent quota.
