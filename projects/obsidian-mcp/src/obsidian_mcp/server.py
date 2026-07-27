"""HTTP MCP server. Auth is a wrapper, not a route: nothing behind it is reachable unauthed."""
import hmac
import os
from pathlib import Path

from fastmcp import FastMCP

from .tools import ObsidianTools


class _AuthMiddleware:
    """Pure-ASGI bearer check. Everything, including /mcp discovery, needs the token."""

    def __init__(self, app, token: str):
        self.app, self.expect = app, f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            found = [v for k, v in scope["headers"] if k == b"authorization"]
            # Exactly one header, compared in constant time. Two headers are
            # ambiguous (a dict() over the list would silently take the last),
            # and a plain != leaks the token a byte at a time through timing.
            ok = len(found) == 1 and hmac.compare_digest(found[0], self.expect)
            if not ok:
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body",
                            "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def build_app(obsidian_root: Path, token: str):
    t = ObsidianTools(obsidian_root)
    mcp = FastMCP("obsidian")
    mcp.tool(t.obsidian_search)
    mcp.tool(t.obsidian_read)
    mcp.tool(t.obsidian_backlinks)
    mcp.tool(t.obsidian_neighbors)
    mcp.tool(t.obsidian_capture)
    mcp.tool(t.obsidian_log_daily)
    mcp.tool(t.obsidian_propose)
    mcp.tool(t.obsidian_status)
    return _AuthMiddleware(mcp.http_app(path="/mcp"), token)


def main():
    import uvicorn

    root = Path(os.environ["OBSIDIAN_ROOT"])
    token = os.environ["OBSIDIAN_MCP_TOKEN"]
    uvicorn.run(build_app(root, token), host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    main()
