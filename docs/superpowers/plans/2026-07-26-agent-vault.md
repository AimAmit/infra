# Agent Operations Vault Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tiered Obsidian vault (`rw`/`ro`/`private`) at `/Users/tnluser/obsidian/obsidian-vault`, served to hermes through a restricted vault-mcp service, with an always-on cluster copy synced over the tailnet.

**Architecture:** Python package `vault_mcp` implements containment (path rules, O_EXCL create-only writes, rate caps), a markdown link index, and a FastMCP HTTP server with bearer auth. Phase 1 runs it on the Mac as a CLI over hermes's existing SSH. Phase 2 containers it, deploys via kustomize + ArgoCD in namespace `agent-knowledge`, and pairs Syncthing (Mac ⇄ cluster) with directional shares enforcing the tiers.

**Tech Stack:** Python 3.13, `fastmcp`, `uvicorn`, `pytest`, Docker (ghcr.io), Syncthing 1.29.x, kustomize, ArgoCD, Tailscale operator.

**Spec:** `docs/superpowers/specs/2026-07-26-agent-vault-design.md`

## Global Constraints

- Vault root (Mac): `/Users/tnluser/obsidian/obsidian-vault`; tiers are the top-level dirs `rw/`, `ro/`, `private/`.
- Paths passed to tools are tier-prefixed relatives: `rw/Daily/x.md`, `ro/Work/y.md`. `private/` is never a valid tier.
- Only `.md` files readable/creatable. No delete, rename, or modify tool anywhere.
- All writes `O_CREAT|O_EXCL`; 64 KiB/note cap; 30 creates/hour cap.
- No secrets in git — repo is public. Tokens minted out-of-band with kubectl.
- Cluster: ClusterIP only for vault-mcp (no tailscale annotation, no ingress). Syncthing Service exposes sync port 22000 only; GUI 8384 reachable only by port-forward.
- Syncthing: global discovery OFF, relays OFF, NAT traversal OFF, both sides; folder IDs `vault-rw` (Send & Receive), `vault-ro` (Mac: Send Only, cluster: Receive Only).
- PVC `agent-vault-data`: 5Gi, `argocd.argoproj.io/sync-options: Prune=false` (local-path has no expansion and Delete reclaim).
- k8s workloads: runAsNonRoot 1000, fsGroup 1000, caps dropped, readOnlyRootFilesystem where the image allows, `automountServiceAccountToken: false`, pinned image tags.
- ArgoCD pattern: `cluster/agent-knowledge/` kustomize + `bootstrap/apps/agent-knowledge.yaml`, matching hermes/codex-lb.
- Python code lives at `projects/vault-mcp/`; dev loop: `uv venv && uv pip install -e '.[dev]'`, tests with `pytest`.

---

## Phase A — vault_mcp package

### Task 1: Package scaffold + path containment

**Files:**
- Create: `projects/vault-mcp/pyproject.toml`
- Create: `projects/vault-mcp/src/vault_mcp/__init__.py` (empty)
- Create: `projects/vault-mcp/src/vault_mcp/paths.py`
- Test: `projects/vault-mcp/tests/test_paths.py`

**Interfaces:**
- Produces: `paths.resolve(vault_root: Path, ref: str) -> Path` — validates a tier-prefixed reference, returns absolute path. Raises `paths.PathViolation(msg)` on any rule breach. `paths.TIERS = ("rw", "ro")`.

- [ ] **Step 1: Write pyproject**

```toml
[project]
name = "vault-mcp"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastmcp>=2.10", "uvicorn>=0.30"]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27"]

[project.scripts]
vault-cli = "vault_mcp.cli:main"
vault-mcp-serve = "vault_mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/vault_mcp"]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_paths.py
import os
import pytest
from pathlib import Path
from vault_mcp.paths import resolve, PathViolation


@pytest.fixture
def vault(tmp_path):
    for t in ("rw", "ro", "private"):
        (tmp_path / t).mkdir()
    return tmp_path


def test_valid_rw_path(vault):
    p = resolve(vault, "rw/Daily/2026-07-26.md")
    assert p == (vault / "rw/Daily/2026-07-26.md").resolve()

def test_private_tier_rejected(vault):
    with pytest.raises(PathViolation):
        resolve(vault, "private/Journal/x.md")

def test_traversal_rejected(vault):
    with pytest.raises(PathViolation):
        resolve(vault, "rw/../private/x.md")

def test_absolute_rejected(vault):
    with pytest.raises(PathViolation):
        resolve(vault, "/etc/passwd")

def test_dotfile_rejected(vault):
    with pytest.raises(PathViolation):
        resolve(vault, "rw/.obsidian/app.json")

def test_non_md_rejected(vault):
    with pytest.raises(PathViolation):
        resolve(vault, "rw/script.sh")

def test_null_byte_rejected(vault):
    with pytest.raises(PathViolation):
        resolve(vault, "rw/a\x00b.md")

def test_symlink_escape_rejected(vault):
    (vault / "private/secret.md").write_text("s")
    os.symlink(vault / "private", vault / "rw/leak")
    with pytest.raises(PathViolation):
        resolve(vault, "rw/leak/secret.md")
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `cd projects/vault-mcp && uv venv && uv pip install -e '.[dev]' && .venv/bin/pytest tests/test_paths.py -q`
Expected: FAIL — `ModuleNotFoundError: vault_mcp.paths` (or ImportError).

- [ ] **Step 4: Implement paths.py**

```python
# src/vault_mcp/paths.py
"""Containment: every path argument passes through resolve() before any I/O."""
from pathlib import Path

TIERS = ("rw", "ro")


class PathViolation(Exception):
    pass


def resolve(vault_root: Path, ref: str) -> Path:
    if "\x00" in ref or "\\" in ref:
        raise PathViolation("forbidden characters in path")
    p = Path(ref)
    if p.is_absolute():
        raise PathViolation("absolute paths not allowed")
    parts = p.parts
    if len(parts) < 2 or parts[0] not in TIERS:
        raise PathViolation(f"path must start with a tier: {TIERS}")
    if any(part == ".." for part in parts):
        raise PathViolation("path traversal not allowed")
    if any(part.startswith(".") for part in parts[1:]):
        raise PathViolation("dotfiles not allowed")
    if p.suffix != ".md":
        raise PathViolation("only .md files")
    tier_root = (vault_root / parts[0]).resolve()
    full = (vault_root / p).resolve()  # follows symlinks -> catches escapes
    if not full.is_relative_to(tier_root):
        raise PathViolation("resolved path escapes tier root")
    return full
```

- [ ] **Step 5: Run tests, verify pass** — `.venv/bin/pytest tests/test_paths.py -q` → 8 passed.

- [ ] **Step 6: Commit**

```bash
git add projects/vault-mcp
git commit -m "feat(vault-mcp): package scaffold with path containment"
```

### Task 2: Create-only writer with caps

**Files:**
- Create: `projects/vault-mcp/src/vault_mcp/writes.py`
- Test: `projects/vault-mcp/tests/test_writes.py`

**Interfaces:**
- Consumes: nothing (paths are pre-resolved by caller).
- Produces: `writes.Writer(clock=time.monotonic)` with `.create(path: Path, content: str) -> Path` (returns actual path written — suffixed `-2`, `-3`… if taken). Raises `writes.WriteLimit`. Constants `MAX_BYTES = 65536`, `MAX_PER_HOUR = 30`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_writes.py
import pytest
from vault_mcp.writes import Writer, WriteLimit, MAX_PER_HOUR


def test_creates_file(tmp_path):
    out = Writer().create(tmp_path / "a.md", "hello")
    assert out.read_text() == "hello"

def test_never_overwrites(tmp_path):
    w = Writer()
    first = w.create(tmp_path / "a.md", "one")
    second = w.create(tmp_path / "a.md", "two")
    assert second != first
    assert second.name == "a-2.md"
    assert first.read_text() == "one"

def test_size_cap(tmp_path):
    with pytest.raises(WriteLimit):
        Writer().create(tmp_path / "big.md", "x" * 70000)

def test_rate_cap(tmp_path):
    t = [0.0]
    w = Writer(clock=lambda: t[0])
    for i in range(MAX_PER_HOUR):
        w.create(tmp_path / f"n{i}.md", "ok")
    with pytest.raises(WriteLimit):
        w.create(tmp_path / "over.md", "no")
    t[0] = 3601.0  # window rolls over
    w.create(tmp_path / "later.md", "ok")
```

- [ ] **Step 2: Run, verify fail** — `.venv/bin/pytest tests/test_writes.py -q` → ImportError.

- [ ] **Step 3: Implement writes.py**

```python
# src/vault_mcp/writes.py
"""Create-only writes. O_EXCL is the overwrite guard - a syscall, not a convention."""
import os
import time
from collections import deque
from pathlib import Path

MAX_BYTES = 65536
MAX_PER_HOUR = 30


class WriteLimit(Exception):
    pass


class Writer:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._stamps: deque[float] = deque()

    def _check_rate(self) -> None:
        now = self._clock()
        while self._stamps and now - self._stamps[0] > 3600:
            self._stamps.popleft()
        if len(self._stamps) >= MAX_PER_HOUR:
            raise WriteLimit(f"rate cap: {MAX_PER_HOUR} notes/hour")
        self._stamps.append(now)

    def create(self, path: Path, content: str) -> Path:
        data = content.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise WriteLimit(f"note exceeds {MAX_BYTES} bytes")
        self._check_rate()
        path.parent.mkdir(parents=True, exist_ok=True)
        target, n = path, 1
        while True:
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                break
            except FileExistsError:
                n += 1
                target = path.with_name(f"{path.stem}-{n}.md")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return target
```

- [ ] **Step 4: Run, verify pass** — 4 passed.

- [ ] **Step 5: Commit** — `git add -A projects/vault-mcp && git commit -m "feat(vault-mcp): create-only writer with size and rate caps"`

### Task 3: Link index — search, neighbors, backlinks

**Files:**
- Create: `projects/vault-mcp/src/vault_mcp/index.py`
- Test: `projects/vault-mcp/tests/test_index.py`

**Interfaces:**
- Consumes: `paths.TIERS`.
- Produces:
  - `index.search(vault_root: Path, query: str, limit: int = 20) -> list[dict]` — case-insensitive substring over `rw/`+`ro/` `.md` files; dicts `{"path": "rw/x.md", "line": int, "snippet": str}`.
  - `index.neighbors(vault_root: Path, ref: str) -> dict` — `{"links": [...], "tags": [...], "frontmatter": {...}}` for one note.
  - `index.backlinks(vault_root: Path, ref: str) -> list[str]` — tier-prefixed paths of notes whose `[[links]]` point at `ref` (match by full relative path without `.md`, or by bare stem, Obsidian-style).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_index.py
import pytest
from vault_mcp.index import search, neighbors, backlinks


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "rw/Daily").mkdir(parents=True)
    (tmp_path / "ro/People").mkdir(parents=True)
    (tmp_path / "private").mkdir()
    (tmp_path / "rw/Daily/2026-07-26.md").write_text(
        "---\ntags: [daily]\n---\nMet [[People/Alice]] about #infra rollout"
    )
    (tmp_path / "ro/People/Alice.md").write_text("# Alice\nWorks on [[Daily/2026-07-26]]")
    (tmp_path / "private/secret.md").write_text("infra password hint")
    (tmp_path / "rw/.obsidian.md").write_text("infra junk")
    return tmp_path


def test_search_hits_both_tiers_not_private(vault):
    hits = search(vault, "infra")
    paths = {h["path"] for h in hits}
    assert "rw/Daily/2026-07-26.md" in paths
    assert not any(p.startswith("private") for p in paths)
    assert not any(".obsidian" in p for p in paths)

def test_neighbors(vault):
    n = neighbors(vault, "rw/Daily/2026-07-26.md")
    assert "People/Alice" in n["links"]
    assert "infra" in n["tags"]
    assert n["frontmatter"].get("tags") == ["daily"]

def test_backlinks_by_stem(vault):
    assert backlinks(vault, "ro/People/Alice.md") == ["rw/Daily/2026-07-26.md"]

def test_backlinks_by_path(vault):
    assert backlinks(vault, "rw/Daily/2026-07-26.md") == ["ro/People/Alice.md"]
```

- [ ] **Step 2: Run, verify fail** — ImportError.

- [ ] **Step 3: Implement index.py**

```python
# src/vault_mcp/index.py
"""Markdown link graph. Full rescan per call - personal vault, thousands of files max."""
import re
from pathlib import Path

from .paths import TIERS, resolve

WIKILINK = re.compile(r"\[\[([^\]|#]+)")
TAG = re.compile(r"(?<!\S)#([A-Za-z][\w/-]*)")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def _iter_notes(vault_root: Path):
    for tier in TIERS:
        root = vault_root / tier
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.md")):
            rel = p.relative_to(vault_root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            yield str(rel), p


def _parse_frontmatter(text: str) -> dict:
    m = FRONTMATTER.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            out[k.strip()] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        else:
            out[k.strip()] = v
    return out


def search(vault_root: Path, query: str, limit: int = 20) -> list[dict]:
    q = query.lower()
    hits = []
    for rel, p in _iter_notes(vault_root):
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if q in line.lower():
                hits.append({"path": rel, "line": i, "snippet": line.strip()[:200]})
                break
        if len(hits) >= limit:
            break
    return hits


def neighbors(vault_root: Path, ref: str) -> dict:
    p = resolve(vault_root, ref)
    text = p.read_text(errors="ignore")
    return {
        "links": [l.strip() for l in WIKILINK.findall(text)],
        "tags": sorted(set(TAG.findall(text))),
        "frontmatter": _parse_frontmatter(text),
    }


def backlinks(vault_root: Path, ref: str) -> list[str]:
    resolve(vault_root, ref)  # validate target even though we only read others
    no_ext = ref[:-3]
    tierless = no_ext.split("/", 1)[1]  # links never carry the tier prefix
    stem = no_ext.rsplit("/", 1)[-1]
    out = []
    for rel, p in _iter_notes(vault_root):
        if rel == ref:
            continue
        for link in WIKILINK.findall(p.read_text(errors="ignore")):
            if link.strip() in (tierless, stem):
                out.append(rel)
                break
    return out
```

- [ ] **Step 4: Run, verify pass** — 4 passed. Also rerun the whole suite: `.venv/bin/pytest -q`.

- [ ] **Step 5: Commit** — `git commit -am "feat(vault-mcp): search, neighbors, backlinks over markdown links"`

### Task 4: MCP tools + HTTP server with bearer auth

**Files:**
- Create: `projects/vault-mcp/src/vault_mcp/tools.py`
- Create: `projects/vault-mcp/src/vault_mcp/server.py`
- Test: `projects/vault-mcp/tests/test_tools.py`
- Test: `projects/vault-mcp/tests/test_server_auth.py`

**Interfaces:**
- Consumes: `paths.resolve`, `writes.Writer`, `index.search/neighbors/backlinks`.
- Produces: `tools.VaultTools(vault_root: Path, writer: Writer | None = None)` with methods `vault_search(query, limit=20)`, `vault_read(ref)`, `vault_backlinks(ref)`, `vault_neighbors(ref)`, `vault_capture(title, content, tags=[])`, `vault_log_daily(content)`, `vault_propose(target_ref, rationale, content)`, `vault_status()`. `server.build_app(vault_root, token) -> ASGI app`; `server.main()` reads env `VAULT_ROOT`, `VAULT_MCP_TOKEN`, `PORT` (default 8080) and runs uvicorn.

- [ ] **Step 1: Write the failing tool tests**

```python
# tests/test_tools.py
import datetime
import pytest
from vault_mcp.tools import VaultTools
from vault_mcp.paths import PathViolation


@pytest.fixture
def tools(tmp_path):
    for d in ("rw/Daily", "rw/Inbox/Agent Captures", "rw/Proposals", "ro"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "ro/note.md").write_text("stable fact")
    return VaultTools(tmp_path)


def test_read_wraps_in_delimiters(tools):
    out = tools.vault_read("ro/note.md")
    assert out.startswith("<<<NOTE ro/note.md>>>")
    assert "stable fact" in out
    assert out.endswith("<<<END NOTE>>>")

def test_read_rejects_private(tools):
    with pytest.raises(PathViolation):
        tools.vault_read("private/x.md")

def test_capture_creates_in_inbox(tools, tmp_path):
    ref = tools.vault_capture("My Idea!", "body", tags=["x"])
    assert ref.startswith("rw/Inbox/Agent Captures/my-idea")
    assert (tmp_path / ref).exists()

def test_capture_twice_distinct_files(tools):
    a = tools.vault_capture("Same", "one")
    b = tools.vault_capture("Same", "two")
    assert a != b

def test_log_daily_numbered_fragments(tools):
    today = datetime.date.today().isoformat()
    a = tools.vault_log_daily("first")
    b = tools.vault_log_daily("second")
    assert a == f"rw/Daily/{today}--agent-1.md"
    assert b == f"rw/Daily/{today}--agent-2.md"

def test_propose_never_touches_target(tools, tmp_path):
    before = (tmp_path / "ro/note.md").read_text()
    ref = tools.vault_propose("ro/note.md", "should mention Y", "new text")
    assert ref.startswith("rw/Proposals/")
    assert (tmp_path / "ro/note.md").read_text() == before

def test_status(tools):
    s = tools.vault_status()
    assert s["notes_ro"] == 1 and "notes_rw" in s
```

- [ ] **Step 2: Run, verify fail** — ImportError.

- [ ] **Step 3: Implement tools.py**

```python
# src/vault_mcp/tools.py
import datetime
import re
import subprocess
from pathlib import Path

from . import index
from .paths import resolve
from .writes import Writer


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:60] or "note"


class VaultTools:
    def __init__(self, vault_root: Path, writer: Writer | None = None):
        self.root = Path(vault_root)
        self.writer = writer or Writer()

    # --- read side ---
    def vault_search(self, query: str, limit: int = 20) -> list[dict]:
        return index.search(self.root, query, limit)

    def vault_read(self, ref: str) -> str:
        p = resolve(self.root, ref)
        body = p.read_text(errors="ignore")
        return f"<<<NOTE {ref}>>>\n{body}\n<<<END NOTE>>>"

    def vault_backlinks(self, ref: str) -> list[str]:
        return index.backlinks(self.root, ref)

    def vault_neighbors(self, ref: str) -> dict:
        return index.neighbors(self.root, ref)

    # --- create-only write side (targets are fixed by the tool, never caller paths) ---
    def _create(self, ref: str, content: str) -> str:
        target = resolve(self.root, ref)
        written = self.writer.create(target, content)
        return str(written.relative_to(self.root))

    def vault_capture(self, title: str, content: str, tags: list[str] | None = None) -> str:
        fm = f"---\ntitle: {title}\ntags: [{', '.join(tags or [])}]\nsource: agent\n---\n"
        return self._create(f"rw/Inbox/Agent Captures/{_slug(title)}.md", fm + content)

    def vault_log_daily(self, content: str) -> str:
        today = datetime.date.today().isoformat()
        existing = list((self.root / "rw/Daily").glob(f"{today}--agent-*.md"))
        n = len(existing) + 1
        stamp = datetime.datetime.now().strftime("%H:%M")
        return self._create(
            f"rw/Daily/{today}--agent-{n}.md",
            f"---\ndate: {today}\ntype: agent-log\n---\n- {stamp} — {content}\n",
        )

    def vault_propose(self, target_ref: str, rationale: str, content: str) -> str:
        resolve(self.root, target_ref)  # target must be valid, but is never written
        today = datetime.date.today().isoformat()
        body = (
            f"---\ntarget: \"[[{target_ref[:-3]}]]\"\ndate: {today}\nsource: agent\n---\n"
            f"## Rationale\n{rationale}\n\n## Proposed content\n{content}\n"
        )
        return self._create(f"rw/Proposals/{today}-{_slug(target_ref)}.md", body)

    def vault_status(self) -> dict:
        def count(tier: str) -> int:
            d = self.root / tier
            return sum(1 for _ in d.rglob("*.md")) if d.is_dir() else 0

        last_commit = None
        if (self.root / "rw/.git").is_dir():
            r = subprocess.run(
                ["git", "-C", str(self.root / "rw"), "log", "-1", "--format=%cI"],
                capture_output=True, text=True,
            )
            last_commit = r.stdout.strip() or None
        return {"notes_rw": count("rw"), "notes_ro": count("ro"), "last_git_commit": last_commit}
```

- [ ] **Step 4: Run tool tests, verify pass** — 7 passed.

- [ ] **Step 5: Write the failing auth test**

```python
# tests/test_server_auth.py
import httpx
import pytest
from vault_mcp.server import build_app


@pytest.fixture
def client(tmp_path):
    (tmp_path / "rw").mkdir(); (tmp_path / "ro").mkdir()
    app = build_app(tmp_path, token="sekrit")
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


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
```

Add to `pyproject.toml` dev deps: `"anyio>=4"`, and `tests/conftest.py`:

```python
import pytest

@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 6: Run, verify fail** — ImportError on `build_app`.

- [ ] **Step 7: Implement server.py**

```python
# src/vault_mcp/server.py
import os
from pathlib import Path

from fastmcp import FastMCP

from .tools import VaultTools


class _AuthMiddleware:
    """Pure-ASGI bearer check. Everything, including /mcp discovery, needs the token."""

    def __init__(self, app, token: str):
        self.app, self.expect = app, f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization")
            if auth != self.expect:
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body",
                            "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def build_app(vault_root: Path, token: str):
    t = VaultTools(vault_root)
    mcp = FastMCP("vault")
    mcp.tool(t.vault_search)
    mcp.tool(t.vault_read)
    mcp.tool(t.vault_backlinks)
    mcp.tool(t.vault_neighbors)
    mcp.tool(t.vault_capture)
    mcp.tool(t.vault_log_daily)
    mcp.tool(t.vault_propose)
    mcp.tool(t.vault_status)
    return _AuthMiddleware(mcp.http_app(path="/mcp"), token)


def main():
    import uvicorn

    root = Path(os.environ["VAULT_ROOT"])
    token = os.environ["VAULT_MCP_TOKEN"]
    uvicorn.run(build_app(root, token), host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    main()
```

Note: if `mcp.http_app(path="/mcp")` errors on this fastmcp version, use `mcp.http_app()` and adjust the test URL to the app's default path (`/mcp` is the fastmcp ≥2.10 default).

- [ ] **Step 8: Run full suite** — `.venv/bin/pytest -q` → all pass.

- [ ] **Step 9: Commit** — `git commit -am "feat(vault-mcp): MCP tools and HTTP server with bearer auth"`

### Task 5: CLI for the SSH phase

**Files:**
- Create: `projects/vault-mcp/src/vault_mcp/cli.py`
- Test: `projects/vault-mcp/tests/test_cli.py`

**Interfaces:**
- Consumes: `VaultTools`.
- Produces: console script `vault-cli` — `vault-cli --root PATH <tool> [args...]`, prints one JSON document to stdout, exit 0; on violation prints `{"error": msg}` and exit 1. Subcommands mirror the tools: `search QUERY`, `read REF`, `backlinks REF`, `neighbors REF`, `capture TITLE CONTENT [--tags a,b]`, `log-daily CONTENT`, `propose TARGET RATIONALE CONTENT`, `status`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
import json
from vault_mcp.cli import run


def test_cli_read(tmp_path, capsys):
    (tmp_path / "rw").mkdir(); (tmp_path / "ro").mkdir()
    (tmp_path / "ro/a.md").write_text("hi")
    assert run(["--root", str(tmp_path), "read", "ro/a.md"]) == 0
    assert "hi" in capsys.readouterr().out

def test_cli_violation_exit_1(tmp_path, capsys):
    (tmp_path / "rw").mkdir(); (tmp_path / "ro").mkdir()
    assert run(["--root", str(tmp_path), "read", "private/x.md"]) == 1
    assert "error" in json.loads(capsys.readouterr().out)

def test_cli_capture(tmp_path, capsys):
    (tmp_path / "rw").mkdir(); (tmp_path / "ro").mkdir()
    assert run(["--root", str(tmp_path), "capture", "T", "body", "--tags", "a,b"]) == 0
    ref = json.loads(capsys.readouterr().out)["ref"]
    assert (tmp_path / ref).exists()
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement cli.py**

```python
# src/vault_mcp/cli.py
import argparse
import json
import sys
from pathlib import Path

from .paths import PathViolation
from .tools import VaultTools
from .writes import WriteLimit


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="vault-cli")
    ap.add_argument("--root", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("search").add_argument("query")
    sub.add_parser("read").add_argument("ref")
    sub.add_parser("backlinks").add_argument("ref")
    sub.add_parser("neighbors").add_argument("ref")
    cap = sub.add_parser("capture")
    cap.add_argument("title"); cap.add_argument("content"); cap.add_argument("--tags", default="")
    sub.add_parser("log-daily").add_argument("content")
    pro = sub.add_parser("propose")
    pro.add_argument("target"); pro.add_argument("rationale"); pro.add_argument("content")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    t = VaultTools(Path(a.root))
    try:
        out = {
            "search": lambda: t.vault_search(a.query),
            "read": lambda: t.vault_read(a.ref),
            "backlinks": lambda: t.vault_backlinks(a.ref),
            "neighbors": lambda: t.vault_neighbors(a.ref),
            "capture": lambda: {"ref": t.vault_capture(
                a.title, a.content, [x for x in a.tags.split(",") if x])},
            "log-daily": lambda: {"ref": t.vault_log_daily(a.content)},
            "propose": lambda: {"ref": t.vault_propose(a.target, a.rationale, a.content)},
            "status": lambda: t.vault_status(),
        }[a.cmd]()
    except (PathViolation, WriteLimit) as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(out) if not isinstance(out, str) else json.dumps({"content": out}))
    return 0


def main():
    sys.exit(run())
```

- [ ] **Step 4: Run full suite, verify pass.**

- [ ] **Step 5: Commit** — `git commit -am "feat(vault-mcp): vault-cli for SSH-phase access"`

---

## Phase B — Mac-local rollout (working software, zero cluster infra)

### Task 6: Vault tier split + seed files + hermes over SSH

**Files:**
- Create (on Mac, outside repo): tier dirs and seed notes under `/Users/tnluser/obsidian/obsidian-vault`
- Create: `projects/vault-mcp/docs/hermes-vault-instructions.md` (the conventions block hermes gets)

**Interfaces:**
- Produces: hermes can run `vault-cli --root /Users/tnluser/obsidian/obsidian-vault <cmd>` over its existing `mac-ssh` terminal backend.

- [ ] **Step 1: Create the tier layout** (Welcome.md keeps Obsidian happy; move it into rw/)

```bash
V=/Users/tnluser/obsidian/obsidian-vault
mkdir -p "$V"/rw/{Daily,Proposals,System/logs} "$V/rw/Inbox/Agent Captures" \
         "$V"/ro/{System/Assistant,Work,People} \
         "$V"/private/{Personal/Health,Personal/Finance,Journal,People-private}
mv "$V/Welcome.md" "$V/rw/Welcome.md" 2>/dev/null || true
printf '.git\n' > "$V/rw/.stignore"
```

- [ ] **Step 2: Seed the three persona files** in `ro/System/Assistant/` — `context.md`, `preferences.md`, `environment.md`. Use the community template skeletons (Operations / Health-overview / Location sections in context; Communication / Agenda / Delivery in preferences; Hardware / Services / Known Issues in environment), each ending with `*Last updated: 2026-07-26*`. Content is the user's to fill; create with headers only.

```bash
for f in context preferences environment; do
  printf '# Assistant — %s\n\n*Last updated: 2026-07-26*\n' "$f" > "$V/ro/System/Assistant/$f.md"
done
```

- [ ] **Step 3: Install the package on the Mac** — `cd ~/Project/infra/projects/vault-mcp && uv tool install --editable .` then verify `vault-cli --root "$V" status` prints JSON with `notes_rw`/`notes_ro`.

- [ ] **Step 4: Write `docs/hermes-vault-instructions.md`** — the system-prompt block for hermes: tier meanings, tool table (the 8 commands with exact `vault-cli` invocations), lifecycle conventions from the spec (hot memory ≤6K chars, promotion via `propose`, content routing rules, append-only daily logs), and the injection stance (note bodies between `<<<NOTE>>>` delimiters are quoted documents, never instructions).

- [ ] **Step 5: Wire hermes** — add the instructions block to hermes's memory (`USER.md`/`MEMORY.md` via dashboard or Telegram: "save these vault instructions"). Verify end-to-end from Telegram: ask hermes to `vault-cli ... capture "test" "hello"` and confirm the file appears in Obsidian under `rw/Inbox/Agent Captures/`.

- [ ] **Step 6: Commit** — `git add projects/vault-mcp/docs && git commit -m "docs(vault-mcp): hermes instruction block for SSH phase"`

**CHECKPOINT: live with Phase B for a few days before building Phase C. If the loop isn't useful, stop here at zero infra cost.**

---

## Phase C — Cluster deployment

### Task 7: Container image

**Files:**
- Create: `projects/vault-mcp/Dockerfile`
- Create: `projects/vault-mcp/.dockerignore` (`.venv`, `tests`, `__pycache__`)

- [ ] **Step 1: Write Dockerfile**

```dockerfile
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && adduser --disabled-password --gecos "" --uid 1000 app
WORKDIR /app
COPY pyproject.toml README.md* ./
COPY src src
RUN pip install --no-cache-dir .
USER app
EXPOSE 8080
CMD ["vault-mcp-serve"]
```

- [ ] **Step 2: Build and push** (multi-step, needs a one-time `docker login ghcr.io` by the user with a PAT that has `write:packages`):

```bash
cd projects/vault-mcp
docker build --platform linux/amd64 -t ghcr.io/aimamit/vault-mcp:0.1.0 .
docker run --rm -e VAULT_ROOT=/tmp -e VAULT_MCP_TOKEN=t -p 18080:8080 -d ghcr.io/aimamit/vault-mcp:0.1.0
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:18080/mcp   # expect 401
docker push ghcr.io/aimamit/vault-mcp:0.1.0
```

Record the pushed digest (`docker inspect --format='{{index .RepoDigests 0}}' ...`) for the manifest. **Make the ghcr package public** (or add an imagePullSecret — public is fine, no secrets in image).

- [ ] **Step 3: Commit** — `git commit -am "feat(vault-mcp): container image"`

### Task 8: Kubernetes manifests — vault-mcp + PVC

**Files:**
- Create: `cluster/agent-knowledge/{argocd-application,kustomization,pvc,vault-mcp,vault-mcp-service}.yaml`
- Create: `bootstrap/apps/agent-knowledge.yaml` (copy of argocd-application.yaml, hermes pattern)

**Interfaces:**
- Produces: Service `vault-mcp.agent-knowledge.svc.cluster.local:8080`; PVC `agent-vault-data` mounted with subPaths `rw` (writable) and `ro` (readOnly) — plus a git-history sidecar committing `rw/` every 5 min.

- [ ] **Step 1: pvc.yaml** — name `agent-vault-data`, 5Gi, RWO, annotation `argocd.argoproj.io/sync-options: Prune=false` with the encryption-key-style comment explaining why (only always-on copy + git history; local-path reclaim is Delete).

- [ ] **Step 2: vault-mcp.yaml** — Deployment, 1 replica, `strategy: Recreate`, image **digest-pinned** from Task 7, hardening block copied from `cluster/codex-lb/deployment.yaml` (runAsNonRoot 1000, fsGroup 1000, seccomp RuntimeDefault, drop ALL, no SA token, readOnlyRootFilesystem + `/tmp` emptyDir). Env: `VAULT_ROOT=/vault`, `VAULT_MCP_TOKEN` from secret `vault-mcp-secrets` key `token`. VolumeMounts: PVC subPath `rw` → `/vault/rw`; PVC subPath `ro` → `/vault/ro` **`readOnly: true`**. Second container `git-history`, image `alpine/git:2.45.2`, same securityContext:

```yaml
command: ["sh", "-c"]
args:
  - |
    cd /vault/rw
    [ -d .git ] || { git init -q -b main; git config user.email agent@cluster; git config user.name vault-history; }
    while true; do
      git add -A >/dev/null 2>&1
      git commit -qm "agent writes $(date -u +%FT%TZ)" >/dev/null 2>&1 || true
      sleep 300
    done
```

with PVC subPath `rw` mounted read-write at `/vault/rw`.

- [ ] **Step 3: vault-mcp-service.yaml** — ClusterIP, port 8080 → 8080, **no tailscale annotations, ever** (comment it like the codex-lb funnel warning). kustomization lists pvc, vault-mcp, vault-mcp-service.

- [ ] **Step 4: Validate + secret** — `kubectl create namespace agent-knowledge`; user mints token out-of-band:

```
! TOKEN=$(openssl rand -base64 32) && kubectl -n agent-knowledge create secret generic vault-mcp-secrets --from-literal=token="$TOKEN" && kubectl -n hermes patch secret hermes-secrets --type merge -p "{\"stringData\":{\"vault-mcp-token\":\"$TOKEN\"}}" && unset TOKEN
```

Then `kubectl apply --dry-run=server -k cluster/agent-knowledge` → 4 objects created (dry run).

- [ ] **Step 5: Commit both app files + push; verify ArgoCD** — `kubectl -n argocd get application agent-knowledge` → Synced/Healthy; pod 2/2 Running; in-pod check `kubectl -n agent-knowledge exec deploy/vault-mcp -c vault-mcp -- sh -c 'touch /vault/ro/x 2>&1'` → "Read-only file system".

- [ ] **Step 6: Seed the PVC** — one-shot copy of current Mac tiers:

```bash
V=/Users/tnluser/obsidian/obsidian-vault
kubectl -n agent-knowledge cp "$V/rw" "$(kubectl -n agent-knowledge get pod -l app=vault-mcp -o name | cut -d/ -f2)":/vault/ -c git-history
kubectl -n agent-knowledge cp "$V/ro" "$(kubectl -n agent-knowledge get pod -l app=vault-mcp -o name | cut -d/ -f2)":/vault/ -c git-history
```

(`git-history` container has the writable `rw` mount and a shell; `kubectl cp` into `/vault/ro` works because the *git-history* container mounts subPath `ro` read-write — give it both mounts rw.) Verify `vault_status` counts match the Mac.

### Task 9: Syncthing — cluster side

**Files:**
- Create: `cluster/agent-knowledge/syncthing.yaml`, `cluster/agent-knowledge/syncthing-service.yaml`; add to kustomization.

- [ ] **Step 1: syncthing.yaml** — Deployment, 1 replica, Recreate, image `syncthing/syncthing:1.29.7` (digest-pin after first pull), hardening block as usual (syncthing image runs fine non-root with fsGroup; `STHOME=/var/syncthing/config`). Env: `STNODEFAULTFOLDER=true`, `STGUIADDRESS=127.0.0.1:8384` — **GUI loopback-only; reach it with port-forward, never a Service** (codex-lb 1455 reasoning). Mounts: PVC subPaths `rw`→`/var/syncthing/vault/rw`, `ro`→`/var/syncthing/vault/ro`; config on its own 1Gi PVC `syncthing-config` (also Prune=false — it holds the device key).

- [ ] **Step 2: syncthing-service.yaml** — port 22000 TCP only, annotations `tailscale.com/expose: "true"`, `tailscale.com/hostname: "vault-sync"`.

- [ ] **Step 3: Commit, push, sync.** Pod Running; `vault-sync` appears in tailnet.

- [ ] **Step 4: Harden via CLI** (inside the pod):

```bash
kubectl -n agent-knowledge exec deploy/syncthing -- syncthing cli config options global-ann-enabled set false
kubectl -n agent-knowledge exec deploy/syncthing -- syncthing cli config options relays-enabled set false
kubectl -n agent-knowledge exec deploy/syncthing -- syncthing cli config options natenabled set false
kubectl -n agent-knowledge exec deploy/syncthing -- syncthing cli config options local-ann-enabled set false
kubectl -n agent-knowledge exec deploy/syncthing -- syncthing cli config gui password set   # user does this via port-forward GUI instead if it prompts
kubectl -n agent-knowledge exec deploy/syncthing -- syncthing cli show system | head -3     # note the device ID
```

### Task 10: Syncthing — Mac side + pairing

- [ ] **Step 1: Install** — user runs `! brew install syncthing && brew services start syncthing`; open `http://127.0.0.1:8384`; set GUI user+password immediately (Settings → GUI).

- [ ] **Step 2: Harden Mac side** — Settings → Connections: uncheck Global Discovery, Relaying, NAT traversal; keep Local Discovery off too. Sync Protocol Listen Address: `tcp://0.0.0.0:22000`.

- [ ] **Step 3: Pair** — Mac: Add Remote Device → cluster device ID, address `tcp://vault-sync.tail94c55.ts.net:22000`. Cluster (port-forward GUI): accept device / add Mac's ID with dynamic address.

- [ ] **Step 4: Shares** —
  - Mac: Add Folder id `vault-rw`, path `/Users/tnluser/obsidian/obsidian-vault/rw`, type **Send & Receive**, share with cluster.
  - Mac: Add Folder id `vault-ro`, path `.../ro`, type **Send Only**, share with cluster.
  - Cluster: accept both; paths `/var/syncthing/vault/rw` (Send & Receive) and `/var/syncthing/vault/ro` (**Receive Only**).
  - **`private/` is in no share. Verify the cluster share list contains exactly `vault-rw`, `vault-ro`.**

- [ ] **Step 5: Sync test** — create `rw/sync-test.md` in Obsidian → appears in pod ≤ 60s; delete it; touch a file inside cluster `ro/` → Syncthing GUI shows "Revert Local Changes" and the file never reaches the Mac; revert it.

### Task 11: Switch hermes to the cluster MCP

**Files:**
- Modify: `cluster/hermes/configmap.yaml` (add `mcp_servers` block)
- Modify: `cluster/hermes/deployment.yaml` (env `VAULT_MCP_TOKEN` from `hermes-secrets/vault-mcp-token`, both containers)

- [ ] **Step 1: configmap** — append:

```yaml
    mcp_servers:
      vault:
        transport: http
        url: http://vault-mcp.agent-knowledge.svc.cluster.local:8080/mcp
        headers:
          Authorization: "Bearer ${VAULT_MCP_TOKEN}"
```

- [ ] **Step 2: deployment env** — same `secretKeyRef` pattern as `OPENAI_API_KEY`, key `vault-mcp-token` (created in Task 8 Step 4).

- [ ] **Step 3: Commit, push, rollout; verify** — `kubectl -n hermes exec deploy/hermes-agent -c hermes -- hermes mcp test vault` → connection OK, 8 tools listed. From Telegram: "search the vault for sync-test" and "capture a note titled hello" → file appears in Obsidian.

- [ ] **Step 4: Update hermes instructions** — replace the `vault-cli` command table from Task 6 with the MCP tool names; move morning-briefing style jobs to `hermes cron add` (each writes via `vault_log_daily`). Uninstall Mac `vault-cli` only after a week of parallel running.

### Task 12: Verification sweep (spec §Verification, all 10 checks)

- [ ] Run every check from the spec verbatim; the ones not already covered above:
  - traversal/symlink probes against the live service (expect `PathViolation` errors in the JSON-RPC response, never content): `vault_read("../../etc/passwd")`, `vault_read("rw/../ro/note.md")` (double-tier), symlink planted in Mac `rw/` pointing at `private/` — after sync, read attempt fails realpath check in the pod.
  - `kubectl -n agent-knowledge exec deploy/vault-mcp -c git-history -- find /vault -path '*private*'` → empty.
  - unauthenticated `curl` to the Service from a debug pod → 401; `kubectl get ingress,httproute -n agent-knowledge` → none; `kubectl get svc -n agent-knowledge -o yaml | grep -i tailscale` → only the syncthing Service.
  - pod delete → vault + `.git` history intact; Mac asleep → hermes reads/writes cluster copy; on wake Obsidian shows the agent's notes.
  - cluster `git -C /vault/rw log --oneline | head` shows commits; `.git` absent on the Mac.
- [ ] Fix anything that fails; re-run; commit any manifest fixes.
- [ ] Update spec Status → `implemented`; commit.

---

## Self-review notes

- Spec coverage: tiers/sync (T6, T9, T10), MCP tools + containment (T1–T4), CLI/SSH phase (T5, T6), lifecycle conventions (T6 Step 4, T11 Step 4), k8s packaging (T7, T8), git history (T8), hardening (T8–T10), verification (T12). Cross-tier link policy is a documentation item — lives in T6 Step 4 instructions.
- Known risk: `fastmcp` API drift on `http_app(path=)` — fallback noted inline in Task 4 Step 7.
- `kubectl cp` seeding requires tar in the git-history image (alpine/git has it).
