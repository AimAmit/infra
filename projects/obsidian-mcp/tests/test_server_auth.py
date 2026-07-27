import httpx
import pytest

from obsidian_mcp.server import build_app


@pytest.fixture
async def client(tmp_path, anyio_backend):
    (tmp_path / "rw").mkdir()
    (tmp_path / "ro").mkdir()
    app = build_app(tmp_path, token="sekrit")
    # fastmcp's session manager lives in a lifespan task group, and
    # ASGITransport never runs lifespan - without this the authorized request
    # dies with "Task group is not initialized" instead of reaching the server.
    inner = app.app
    async with inner.router.lifespan_context(inner):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.anyio
async def test_no_token_401(client):
    r = await client.post("/mcp", json={})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_wrong_token_401(client):
    r = await client.post("/mcp", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_right_token_not_401(client):
    r = await client.post("/mcp", json={}, headers={"Authorization": "Bearer sekrit"})
    assert r.status_code != 401


@pytest.mark.anyio
async def test_duplicate_auth_headers_401(client):
    """Two Authorization headers must be refused, not resolved to the last one.

    A dict() over the ASGI header list silently keeps whichever copy comes
    last, so a proxy or client that appends its own header could override a
    rejected one. Ambiguous auth is failed auth.
    """
    r = await client.post(
        "/mcp",
        json={},
        headers=[("Authorization", "Bearer nope"), ("Authorization", "Bearer sekrit")],
    )
    assert r.status_code == 401
