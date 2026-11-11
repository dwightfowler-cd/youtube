"""Helpers for driving the real server over real stdio with the real MCP client."""
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REAL_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "youtube-mcp"
VENV_SCRIPT = Path(sys.executable).parent / "youtube"

LAUNCHERS = {
    "python -m youtube": [sys.executable, "-m", "youtube"],
    "youtube console script": [str(VENV_SCRIPT)],
}


@dataclass
class Result:
    is_error: bool
    text: str

    @property
    def data(self):
        assert not self.is_error, self.text
        return json.loads(self.text)


def server_env(cache_home, config_home=None) -> dict:
    env = dict(os.environ)
    env["XDG_CACHE_HOME"] = str(cache_home)
    if config_home is not None:
        env["XDG_CONFIG_HOME"] = str(config_home)
    return env


def run_tools(calls, env, launcher=None):
    """Start the real server, run [(tool, args), ...] in ONE session, return Results."""
    cmd = launcher or LAUNCHERS["python -m youtube"]
    params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)

    async def go():
        out = []
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                for name, args in calls:
                    res = await s.call_tool(name, args)
                    out.append(Result(bool(res.isError), res.content[0].text))
        return out

    return anyio.run(go)


def list_tool_names(env, launcher=None):
    cmd = launcher or LAUNCHERS["python -m youtube"]
    params = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)

    async def go():
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return {t.name for t in (await s.list_tools()).tools}

    return anyio.run(go)
