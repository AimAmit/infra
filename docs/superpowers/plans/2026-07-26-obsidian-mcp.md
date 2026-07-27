# Obsidian MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tiered Obsidian vault (`rw`/`ro`/`private`) at `/Users/tnluser/obsidian/obsidian-vault`, served to hermes through a restricted obsidian-mcp service, with an always-on cluster copy synced over the tailnet.

**Architecture:** Python package `obsidian_mcp` implements containment (path rules, O_EXCL create-only writes, rate caps), a markdown link index, and a FastMCP HTTP server with bearer auth. It is containerised, deployed via kustomize + ArgoCD in namespace `agent-knowledge`, and reached by hermes over cluster-internal HTTP. Syncthing (Mac ⇄ cluster) replicates the vault with directional shares enforcing the tiers.

**The SSH route is deliberately not a phase.** hermes must keep working when the Mac is asleep or gone, so the cluster copy is the only access path it ever gets. `obsidian-cli` (Task 5) survives as a Mac-side admin and seeding tool — hermes never calls it.

**Tech Stack:** Python 3.13, `fastmcp`, `uvicorn`, `pytest`, Docker (ghcr.io), Syncthing 1.29.x, kustomize, ArgoCD, Tailscale operator.

**Spec:** `docs/superpowers/specs/2026-07-26-obsidian-mcp-design.md`

## Global Constraints

- Vault root (Mac): `/Users/tnluser/obsidian/obsidian-vault`; tiers are the top-level dirs `rw/`, `ro/`, `private/`.
- Paths passed to tools are tier-prefixed relatives: `rw/Daily/x.md`, `ro/Work/y.md`. `private/` is never a valid tier.
- Only `.md` files readable/creatable. No delete, rename, or modify tool anywhere.
- All writes `O_CREAT|O_EXCL`; 64 KiB/note cap; 30 creates/hour cap.
- No secrets in git — repo is public. Tokens minted out-of-band with kubectl.
- Cluster: ClusterIP only for obsidian-mcp (no tailscale annotation, no ingress). Syncthing Service exposes sync port 22000 only; GUI 8384 reachable only by port-forward.
- Syncthing: global discovery OFF, relays OFF, NAT traversal OFF, both sides; folder IDs `obsidian-rw` (Send & Receive), `obsidian-ro` (Mac: Send Only, cluster: Receive Only).
- PVC `obsidian-data`: 5Gi, `argocd.argoproj.io/sync-options: Prune=false` (local-path has no expansion and Delete reclaim).
- k8s workloads: runAsNonRoot 1000, fsGroup 1000, caps dropped, readOnlyRootFilesystem where the image allows, `automountServiceAccountToken: false`, pinned image tags.
- ArgoCD pattern: `cluster/agent-knowledge/` kustomize + `bootstrap/apps/agent-knowledge.yaml`, matching hermes/codex-lb.
- Python code lives at `projects/obsidian-mcp/`; dev loop: `uv venv && uv pip install -e '.[dev]'`, tests with `pytest`.

---

## Phase A — obsidian_mcp package

### Task 1: Package scaffold + path containment

**Files:**
- Create: `projects/obsidian-mcp/pyproject.toml`
- Create: `projects/obsidian-mcp/src/obsidian_mcp/__init__.py` (empty)
- Create: `projects/obsidian-mcp/src/obsidian_mcp/paths.py`
- Test: `projects/obsidian-mcp/tests/test_paths.py`

**Interfaces:**
- Produces: `paths.resolve(obsidian_root: Path, ref: str) -> Path` — validates a tier-prefixed reference, returns absolute path. Raises `paths.PathViolation(msg)` on any rule breach. `paths.TIERS = ("rw", "ro")`.

- [ ] **Step 1: Write pyproject**

```toml
[project]
name = "obsidian-mcp"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["fastmcp>=2.10", "uvicorn>=0.30"]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27"]

[project.scripts]
obsidian-cli = "obsidian_mcp.cli:main"
obsidian-mcp-serve = "obsidian_mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/obsidian_mcp"]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_paths.py
import os
import pytest
from pathlib import Path
from obsidian_mcp.paths import resolve, PathViolation


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

Run: `cd projects/obsidian-mcp && uv venv && uv pip install -e '.[dev]' && .venv/bin/pytest tests/test_paths.py -q`
Expected: FAIL — `ModuleNotFoundError: obsidian_mcp.paths` (or ImportError).

- [ ] **Step 4: Implement paths.py**

```python
# src/obsidian_mcp/paths.py
"""Containment: every path argument passes through resolve() before any I/O."""
from pathlib import Path

TIERS = ("rw", "ro")


class PathViolation(Exception):
    pass


def resolve(obsidian_root: Path, ref: str) -> Path:
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
    tier_root = (obsidian_root / parts[0]).resolve()
    full = (obsidian_root / p).resolve()  # follows symlinks -> catches escapes
    if not full.is_relative_to(tier_root):
        raise PathViolation("resolved path escapes tier root")
    return full
```

- [ ] **Step 5: Run tests, verify pass** — `.venv/bin/pytest tests/test_paths.py -q` → 8 passed.

- [ ] **Step 6: Commit**

```bash
git add projects/obsidian-mcp
git commit -m "feat(obsidian-mcp): package scaffold with path containment"
```

### Task 2: Create-only writer with caps

**Files:**
- Create: `projects/obsidian-mcp/src/obsidian_mcp/writes.py`
- Test: `projects/obsidian-mcp/tests/test_writes.py`

**Interfaces:**
- Consumes: nothing (paths are pre-resolved by caller).
- Produces: `writes.Writer(clock=time.monotonic)` with `.create(path: Path, content: str) -> Path` (returns actual path written — suffixed `-2`, `-3`… if taken). Raises `writes.WriteLimit`. Constants `MAX_BYTES = 65536`, `MAX_PER_HOUR = 30`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_writes.py
import pytest
from obsidian_mcp.writes import Writer, WriteLimit, MAX_PER_HOUR


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
# src/obsidian_mcp/writes.py
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

- [ ] **Step 5: Commit** — `git add -A projects/obsidian-mcp && git commit -m "feat(obsidian-mcp): create-only writer with size and rate caps"`

### Task 3: Link index — search, neighbors, backlinks

**Files:**
- Create: `projects/obsidian-mcp/src/obsidian_mcp/index.py`
- Test: `projects/obsidian-mcp/tests/test_index.py`

**Interfaces:**
- Consumes: `paths.TIERS`.
- Produces:
  - `index.search(obsidian_root: Path, query: str, limit: int = 20) -> list[dict]` — case-insensitive substring over `rw/`+`ro/` `.md` files; dicts `{"path": "rw/x.md", "line": int, "snippet": str}`.
  - `index.neighbors(obsidian_root: Path, ref: str) -> dict` — `{"links": [...], "tags": [...], "frontmatter": {...}}` for one note.
  - `index.backlinks(obsidian_root: Path, ref: str) -> list[str]` — tier-prefixed paths of notes whose `[[links]]` point at `ref` (match by full relative path without `.md`, or by bare stem, Obsidian-style).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_index.py
import pytest
from obsidian_mcp.index import search, neighbors, backlinks


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
# src/obsidian_mcp/index.py
"""Markdown link graph. Full rescan per call - personal vault, thousands of files max."""
import re
from pathlib import Path

from .paths import TIERS, resolve

WIKILINK = re.compile(r"\[\[([^\]|#]+)")
TAG = re.compile(r"(?<!\S)#([A-Za-z][\w/-]*)")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def _iter_notes(obsidian_root: Path):
    for tier in TIERS:
        root = obsidian_root / tier
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.md")):
            rel = p.relative_to(obsidian_root)
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


def search(obsidian_root: Path, query: str, limit: int = 20) -> list[dict]:
    q = query.lower()
    hits = []
    for rel, p in _iter_notes(obsidian_root):
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if q in line.lower():
                hits.append({"path": rel, "line": i, "snippet": line.strip()[:200]})
                break
        if len(hits) >= limit:
            break
    return hits


def neighbors(obsidian_root: Path, ref: str) -> dict:
    p = resolve(obsidian_root, ref)
    text = p.read_text(errors="ignore")
    return {
        "links": [l.strip() for l in WIKILINK.findall(text)],
        "tags": sorted(set(TAG.findall(text))),
        "frontmatter": _parse_frontmatter(text),
    }


def backlinks(obsidian_root: Path, ref: str) -> list[str]:
    resolve(obsidian_root, ref)  # validate target even though we only read others
    no_ext = ref[:-3]
    tierless = no_ext.split("/", 1)[1]  # links never carry the tier prefix
    stem = no_ext.rsplit("/", 1)[-1]
    out = []
    for rel, p in _iter_notes(obsidian_root):
        if rel == ref:
            continue
        for link in WIKILINK.findall(p.read_text(errors="ignore")):
            if link.strip() in (tierless, stem):
                out.append(rel)
                break
    return out
```

- [ ] **Step 4: Run, verify pass** — 4 passed. Also rerun the whole suite: `.venv/bin/pytest -q`.

- [ ] **Step 5: Commit** — `git commit -am "feat(obsidian-mcp): search, neighbors, backlinks over markdown links"`

### Task 4: MCP tools + HTTP server with bearer auth

**Files:**
- Create: `projects/obsidian-mcp/src/obsidian_mcp/tools.py`
- Create: `projects/obsidian-mcp/src/obsidian_mcp/server.py`
- Test: `projects/obsidian-mcp/tests/test_tools.py`
- Test: `projects/obsidian-mcp/tests/test_server_auth.py`

**Interfaces:**
- Consumes: `paths.resolve`, `writes.Writer`, `index.search/neighbors/backlinks`.
- Produces: `tools.ObsidianTools(obsidian_root: Path, writer: Writer | None = None)` with methods `obsidian_search(query, limit=20)`, `obsidian_read(ref)`, `obsidian_backlinks(ref)`, `obsidian_neighbors(ref)`, `obsidian_capture(title, content, tags=[])`, `obsidian_log_daily(content)`, `obsidian_propose(target_ref, rationale, content)`, `obsidian_status()`. `server.build_app(obsidian_root, token) -> ASGI app`; `server.main()` reads env `OBSIDIAN_ROOT`, `OBSIDIAN_MCP_TOKEN`, `PORT` (default 8080) and runs uvicorn.

- [x] **Step 1: Write the failing tool tests**

```python
# tests/test_tools.py
import datetime
import pytest
from obsidian_mcp.tools import ObsidianTools
from obsidian_mcp.paths import PathViolation


@pytest.fixture
def tools(tmp_path):
    for d in ("rw/Daily", "rw/Inbox/Agent Captures", "rw/Proposals", "ro"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "ro/note.md").write_text("stable fact")
    return ObsidianTools(tmp_path)


def test_read_wraps_in_delimiters(tools):
    out = tools.obsidian_read("ro/note.md")
    assert out.startswith("<<<NOTE ro/note.md>>>")
    assert "stable fact" in out
    assert out.endswith("<<<END NOTE>>>")

def test_read_rejects_private(tools):
    with pytest.raises(PathViolation):
        tools.obsidian_read("private/x.md")

def test_capture_creates_in_inbox(tools, tmp_path):
    ref = tools.obsidian_capture("My Idea!", "body", tags=["x"])
    assert ref.startswith("rw/Inbox/Agent Captures/my-idea")
    assert (tmp_path / ref).exists()

def test_capture_twice_distinct_files(tools):
    a = tools.obsidian_capture("Same", "one")
    b = tools.obsidian_capture("Same", "two")
    assert a != b

def test_log_daily_numbered_fragments(tools):
    today = datetime.date.today().isoformat()
    a = tools.obsidian_log_daily("first")
    b = tools.obsidian_log_daily("second")
    assert a == f"rw/Daily/{today}--agent-1.md"
    assert b == f"rw/Daily/{today}--agent-2.md"

def test_propose_never_touches_target(tools, tmp_path):
    before = (tmp_path / "ro/note.md").read_text()
    ref = tools.obsidian_propose("ro/note.md", "should mention Y", "new text")
    assert ref.startswith("rw/Proposals/")
    assert (tmp_path / "ro/note.md").read_text() == before

def test_status(tools):
    s = tools.obsidian_status()
    assert s["notes_ro"] == 1 and "notes_rw" in s
```

- [x] **Step 2: Run, verify fail** — ImportError.

- [x] **Step 3: Implement tools.py**

```python
# src/obsidian_mcp/tools.py
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


class ObsidianTools:
    def __init__(self, obsidian_root: Path, writer: Writer | None = None):
        self.root = Path(obsidian_root)
        self.writer = writer or Writer()

    # --- read side ---
    def obsidian_search(self, query: str, limit: int = 20) -> list[dict]:
        return index.search(self.root, query, limit)

    def obsidian_read(self, ref: str) -> str:
        p = resolve(self.root, ref)
        body = p.read_text(errors="ignore")
        return f"<<<NOTE {ref}>>>\n{body}\n<<<END NOTE>>>"

    def obsidian_backlinks(self, ref: str) -> list[str]:
        return index.backlinks(self.root, ref)

    def obsidian_neighbors(self, ref: str) -> dict:
        return index.neighbors(self.root, ref)

    # --- create-only write side (targets are fixed by the tool, never caller paths) ---
    def _create(self, ref: str, content: str) -> str:
        target = resolve(self.root, ref)
        written = self.writer.create(target, content)
        return str(written.relative_to(self.root))

    def obsidian_capture(self, title: str, content: str, tags: list[str] | None = None) -> str:
        fm = f"---\ntitle: {title}\ntags: [{', '.join(tags or [])}]\nsource: agent\n---\n"
        return self._create(f"rw/Inbox/Agent Captures/{_slug(title)}.md", fm + content)

    def obsidian_log_daily(self, content: str) -> str:
        today = datetime.date.today().isoformat()
        existing = list((self.root / "rw/Daily").glob(f"{today}--agent-*.md"))
        n = len(existing) + 1
        stamp = datetime.datetime.now().strftime("%H:%M")
        return self._create(
            f"rw/Daily/{today}--agent-{n}.md",
            f"---\ndate: {today}\ntype: agent-log\n---\n- {stamp} — {content}\n",
        )

    def obsidian_propose(self, target_ref: str, rationale: str, content: str) -> str:
        resolve(self.root, target_ref)  # target must be valid, but is never written
        today = datetime.date.today().isoformat()
        body = (
            f"---\ntarget: \"[[{target_ref[:-3]}]]\"\ndate: {today}\nsource: agent\n---\n"
            f"## Rationale\n{rationale}\n\n## Proposed content\n{content}\n"
        )
        return self._create(f"rw/Proposals/{today}-{_slug(target_ref)}.md", body)

    def obsidian_status(self) -> dict:
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

- [x] **Step 4: Run tool tests, verify pass** — 7 passed.

- [x] **Step 5: Write the failing auth test**

```python
# tests/test_server_auth.py
import httpx
import pytest
from obsidian_mcp.server import build_app


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

- [x] **Step 6: Run, verify fail** — ImportError on `build_app`.

- [x] **Step 7: Implement server.py**

```python
# src/obsidian_mcp/server.py
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
            auth = dict(scope["headers"]).get(b"authorization")
            if auth != self.expect:
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
```

Note: if `mcp.http_app(path="/mcp")` errors on this fastmcp version, use `mcp.http_app()` and adjust the test URL to the app's default path (`/mcp` is the fastmcp ≥2.10 default).

- [x] **Step 8: Run full suite** — `.venv/bin/pytest -q` → all pass.

- [x] **Step 9: Commit** — `git commit -am "feat(obsidian-mcp): MCP tools and HTTP server with bearer auth"`

### Task 5: Admin CLI (seeding, inspection, Mac-side debugging — not an agent path)

**Files:**
- Create: `projects/obsidian-mcp/src/obsidian_mcp/cli.py`
- Test: `projects/obsidian-mcp/tests/test_cli.py`

**Interfaces:**
- Consumes: `ObsidianTools`.
- Produces: console script `obsidian-cli` — `obsidian-cli --root PATH <tool> [args...]`, prints one JSON document to stdout, exit 0; on violation prints `{"error": msg}` and exit 1. Subcommands mirror the tools: `search QUERY`, `read REF`, `backlinks REF`, `neighbors REF`, `capture TITLE CONTENT [--tags a,b]`, `log-daily CONTENT`, `propose TARGET RATIONALE CONTENT`, `status`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
import json
from obsidian_mcp.cli import run


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

- [x] **Step 2: Run, verify fail.**

- [x] **Step 3: Implement cli.py**

```python
# src/obsidian_mcp/cli.py
import argparse
import json
import sys
from pathlib import Path

from .paths import PathViolation
from .tools import ObsidianTools
from .writes import WriteLimit


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="obsidian-cli")
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
    t = ObsidianTools(Path(a.root))
    try:
        out = {
            "search": lambda: t.obsidian_search(a.query),
            "read": lambda: t.obsidian_read(a.ref),
            "backlinks": lambda: t.obsidian_backlinks(a.ref),
            "neighbors": lambda: t.obsidian_neighbors(a.ref),
            "capture": lambda: {"ref": t.obsidian_capture(
                a.title, a.content, [x for x in a.tags.split(",") if x])},
            "log-daily": lambda: {"ref": t.obsidian_log_daily(a.content)},
            "propose": lambda: {"ref": t.obsidian_propose(a.target, a.rationale, a.content)},
            "status": lambda: t.obsidian_status(),
        }[a.cmd]()
    except (PathViolation, WriteLimit) as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(out) if not isinstance(out, str) else json.dumps({"content": out}))
    return 0


def main():
    sys.exit(run())
```

- [x] **Step 4: Run full suite, verify pass.**

- [x] **Step 5: Commit** — `git commit -am "feat(obsidian-mcp): obsidian-cli for SSH-phase access"`

---

## Phase B — Mac vault preparation (source of the seed data)

### Task 6: Vault tier split + seed files

**Files:**
- Create (on Mac, outside repo): tier dirs and seed notes under `/Users/tnluser/obsidian/obsidian-vault`

**Interfaces:**
- Produces: a tiered vault on disk that Task 8 Step 6 copies into the PVC and Task 10 shares over Syncthing. Nothing here is an agent access path.

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

- [ ] **Step 3: Install the admin CLI on the Mac** — `cd ~/Project/infra/projects/obsidian-mcp && uv tool install --editable .` then verify `obsidian-cli --root "$V" status` prints JSON with `notes_rw`/`notes_ro`. This is for your own inspection and for sanity-checking containment against the real vault; hermes has no route to it.

- [ ] **Step 4: Decide what goes in `ro/` with the model provider in mind** — `ro/` and `rw/` content becomes model context through codex-lb's pooled ChatGPT accounts. `private/` is the only tier that never leaves your hardware. Sort existing notes accordingly *before* the PVC seed in Task 8, because unsorting them later means deleting from the cluster copy and the git history.

---

## Phase C — Cluster deployment

### Task 7: Container image

**Files:**
- Create: `projects/obsidian-mcp/Dockerfile`
- Create: `projects/obsidian-mcp/.dockerignore` (`.venv`, `tests`, `__pycache__`)

- [x] **Step 1: Write Dockerfile**

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
CMD ["obsidian-mcp-serve"]
```

- [x] **Step 2: Build and push** (multi-step, needs a one-time `docker login ghcr.io` by the user with a PAT that has `write:packages`):

```bash
cd projects/obsidian-mcp
docker buildx build --platform linux/arm64 -t ghcr.io/aimamit/obsidian-mcp:0.1.0 --push .  # node is arm64
docker run --rm -e OBSIDIAN_ROOT=/tmp -e OBSIDIAN_MCP_TOKEN=t -p 18080:8080 -d ghcr.io/aimamit/obsidian-mcp:0.1.0
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:18080/mcp   # expect 401
docker push ghcr.io/aimamit/obsidian-mcp:0.1.0
```

Record the pushed digest (`docker inspect --format='{{index .RepoDigests 0}}' ...`) for the manifest. **Make the ghcr package public** (or add an imagePullSecret — public is fine, no secrets in image).

- [x] **Step 3: Commit** — `git commit -am "feat(obsidian-mcp): container image"`

### Task 8: Kubernetes manifests — obsidian-mcp + PVC

**Files:**
- Create: `cluster/agent-knowledge/{argocd-application,kustomization,pvc,obsidian-mcp,obsidian-mcp-service}.yaml`
- Create: `bootstrap/apps/agent-knowledge.yaml` (copy of argocd-application.yaml, hermes pattern)

**Interfaces:**
- Produces: Service `obsidian-mcp.agent-knowledge.svc.cluster.local:8080`; PVC `obsidian-data` mounted with subPaths `rw` (writable) and `ro` (readOnly) — plus a git-history sidecar committing `rw/` every 5 min.

- [x] **Step 1: pvc.yaml** — name `obsidian-data`, 5Gi, RWO, annotation `argocd.argoproj.io/sync-options: Prune=false` with the encryption-key-style comment explaining why (only always-on copy + git history; local-path reclaim is Delete).

- [x] **Step 2: obsidian-mcp.yaml** — Deployment, 1 replica, `strategy: Recreate`, image **digest-pinned** from Task 7, hardening block copied from `cluster/codex-lb/deployment.yaml` (runAsNonRoot 1000, fsGroup 1000, seccomp RuntimeDefault, drop ALL, no SA token, readOnlyRootFilesystem + `/tmp` emptyDir). Env: `OBSIDIAN_ROOT=/obsidian`, `OBSIDIAN_MCP_TOKEN` from secret `obsidian-mcp-secrets` key `token`. VolumeMounts: PVC subPath `rw` → `/obsidian/rw`; PVC subPath `ro` → `/obsidian/ro` **`readOnly: true`**. Second container `git-history`, image `alpine/git:2.45.2`, same securityContext:

```yaml
command: ["sh", "-c"]
args:
  - |
    cd /obsidian/rw
    [ -d .git ] || { git init -q -b main; git config user.email agent@cluster; git config user.name obsidian-history; }
    while true; do
      git add -A >/dev/null 2>&1
      git commit -qm "agent writes $(date -u +%FT%TZ)" >/dev/null 2>&1 || true
      sleep 300
    done
```

with PVC subPath `rw` mounted read-write at `/obsidian/rw`.

- [x] **Step 3: obsidian-mcp-service.yaml** — ClusterIP, port 8080 → 8080, **no tailscale annotations, ever** (comment it like the codex-lb funnel warning). kustomization lists pvc, obsidian-mcp, obsidian-mcp-service, networkpolicy.

- [x] **Step 3b: networkpolicy.yaml** — the bearer token must not be the only thing between a compromised pod and the notes. Ingress-only policy on `app=obsidian-mcp`, port 8080, from the `hermes` namespace alone:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: obsidian-mcp-ingress
  namespace: agent-knowledge
spec:
  podSelector:
    matchLabels:
      app: obsidian-mcp
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: hermes
      ports:
        - protocol: TCP
          port: 8080
```

First NetworkPolicy in this cluster — **verify the CNI enforces them before trusting it.** Apply, then from a throwaway pod in `default`: `kubectl run probe --rm -it --image=curlimages/curl --restart=Never -- curl -m 5 -s -o /dev/null -w '%{http_code}' http://obsidian-mcp.agent-knowledge:8080/mcp` → must time out, not return 401. A 401 means the CNI is ignoring the policy and this defence does not exist. Note the result in the task report either way.

- [ ] **Step 4: Validate + secret** — `kubectl create namespace agent-knowledge`; user mints token out-of-band:

```
! TOKEN=$(openssl rand -base64 32) && kubectl -n agent-knowledge create secret generic obsidian-mcp-secrets --from-literal=token="$TOKEN" && kubectl -n hermes patch secret hermes-secrets --type merge -p "{\"stringData\":{\"obsidian-mcp-token\":\"$TOKEN\"}}" && unset TOKEN
```

Then `kubectl apply --dry-run=server -k cluster/agent-knowledge` → 4 objects created (dry run).

- [ ] **Step 5: Commit both app files + push; verify ArgoCD** — `kubectl -n argocd get application agent-knowledge` → Synced/Healthy; pod 2/2 Running; in-pod check `kubectl -n agent-knowledge exec deploy/obsidian-mcp -c obsidian-mcp -- sh -c 'touch /obsidian/ro/x 2>&1'` → "Read-only file system".

- [ ] **Step 6: Seed the PVC** — one-shot copy of current Mac tiers:

```bash
V=/Users/tnluser/obsidian/obsidian-vault
kubectl -n agent-knowledge cp "$V/rw" "$(kubectl -n agent-knowledge get pod -l app=obsidian-mcp -o name | cut -d/ -f2)":/obsidian/ -c git-history
kubectl -n agent-knowledge cp "$V/ro" "$(kubectl -n agent-knowledge get pod -l app=obsidian-mcp -o name | cut -d/ -f2)":/obsidian/ -c git-history
```

(`git-history` container has the writable `rw` mount and a shell; `kubectl cp` into `/obsidian/ro` works because the *git-history* container mounts subPath `ro` read-write — give it both mounts rw.) Verify `obsidian_status` counts match the Mac.

### Task 9: Syncthing — cluster side

**Files:**
- Create: `cluster/agent-knowledge/syncthing.yaml`, `cluster/agent-knowledge/syncthing-service.yaml`; add to kustomization.

- [x] **Step 1: syncthing.yaml** — Deployment, 1 replica, Recreate, image `syncthing/syncthing:1.29.7` (digest-pin after first pull), hardening block as usual (syncthing image runs fine non-root with fsGroup; `STHOME=/var/syncthing/config`). Env: `STNODEFAULTFOLDER=true`, `STGUIADDRESS=127.0.0.1:8384` — **GUI loopback-only; reach it with port-forward, never a Service** (codex-lb 1455 reasoning). Mounts: PVC subPaths `rw`→`/var/syncthing/obsidian/rw`, `ro`→`/var/syncthing/obsidian/ro`; config on its own 1Gi PVC `syncthing-config` (also Prune=false — it holds the device key).

- [x] **Step 2: syncthing-service.yaml** — port 22000 TCP only, annotations `tailscale.com/expose: "true"`, `tailscale.com/hostname: "obsidian-sync"`.

- [ ] **Step 3: Commit, push, sync.** Pod Running; `obsidian-sync` appears in tailnet.

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

- [ ] **Step 3: Pair** — Mac: Add Remote Device → cluster device ID, address `tcp://obsidian-sync.tail94c55.ts.net:22000`. Cluster (port-forward GUI): accept device / add Mac's ID with dynamic address.

- [ ] **Step 4: Shares** —
  - Mac: Add Folder id `obsidian-rw`, path `/Users/tnluser/obsidian/obsidian-vault/rw`, type **Send & Receive**, share with cluster.
  - Mac: Add Folder id `obsidian-ro`, path `.../ro`, type **Send Only**, share with cluster.
  - Cluster: accept both; paths `/var/syncthing/obsidian/rw` (Send & Receive) and `/var/syncthing/obsidian/ro` (**Receive Only**).
  - **`private/` is in no share. Verify the cluster share list contains exactly `obsidian-rw`, `obsidian-ro`.**

- [ ] **Step 5: Sync test** — create `rw/sync-test.md` in Obsidian → appears in pod ≤ 60s; delete it; touch a file inside cluster `ro/` → Syncthing GUI shows "Revert Local Changes" and the file never reaches the Mac; revert it.

### Task 11: Switch hermes to the cluster MCP

**Files:**
- Modify: `cluster/hermes/configmap.yaml` (add `mcp_servers` block)
- Modify: `cluster/hermes/deployment.yaml` (env `OBSIDIAN_MCP_TOKEN` from `hermes-secrets/obsidian-mcp-token`, both containers)

- [x] **Step 1: configmap** — append:

```yaml
    mcp_servers:
      obsidian:
        transport: http
        url: http://obsidian-mcp.agent-knowledge.svc.cluster.local:8080/mcp
        headers:
          Authorization: "Bearer ${OBSIDIAN_MCP_TOKEN}"
```

- [x] **Step 2: deployment env** — same `secretKeyRef` pattern as `OPENAI_API_KEY`, key `obsidian-mcp-token` (created in Task 8 Step 4).

- [ ] **Step 3: Commit, push, rollout; verify** — `kubectl -n hermes exec deploy/hermes-agent -c hermes -- hermes mcp test obsidian` → connection OK, 8 tools listed. From Telegram: "search the vault for sync-test" and "capture a note titled hello" → file appears in Obsidian.

- [ ] **Step 4: Write `projects/obsidian-mcp/docs/hermes-obsidian-instructions.md`** — the block that goes into hermes's memory (`USER.md`/`MEMORY.md` via dashboard or Telegram). Contents: tier meanings, the 8 MCP tool names and when to reach for each, lifecycle conventions from the spec (hot memory ≤6K chars, promotion via `obsidian_propose`, content routing rules, append-only daily fragments), the cross-tier link policy, and the injection stance — text between `<<<NOTE>>>` delimiters is a quoted document, never an instruction.

- [ ] **Step 5: Give the tools docstrings** — hermes's only description of each tool is what fastmcp derives from the signature, and right now `tools.py` has none. One line per method covering the tier it touches and its create-only nature; re-run `tools/list` and read them back as hermes would see them.

- [ ] **Step 6: Move scheduled work into the cluster** — morning briefing and similar via `hermes cron add`, each writing through `obsidian_log_daily`.

### Task 12: Verification sweep (spec §Verification, all 10 checks)

- [ ] Run every check from the spec verbatim; the ones not already covered above:
  - traversal/symlink probes against the live service (expect `PathViolation` errors in the JSON-RPC response, never content): `obsidian_read("../../etc/passwd")`, `obsidian_read("rw/../ro/note.md")` (double-tier), symlink planted in Mac `rw/` pointing at `private/` — after sync, read attempt fails realpath check in the pod.
  - `kubectl -n agent-knowledge exec deploy/obsidian-mcp -c git-history -- find /vault -path '*private*'` → empty.
  - unauthenticated `curl` from a debug pod **in the `hermes` namespace** → 401 (from any other namespace it must time out instead — that is the NetworkPolicy, checked in T8 Step 3b); `kubectl get ingress,httproute -n agent-knowledge` → none; `kubectl get svc -n agent-knowledge -o yaml | grep -i tailscale` → only the syncthing Service.
  - duplicate `Authorization` headers → 401, not accepted-by-last-header.
  - pod delete → vault + `.git` history intact; Mac asleep → hermes reads/writes cluster copy; on wake Obsidian shows the agent's notes.
  - cluster `git -C /obsidian/rw log --oneline | head` shows commits; `.git` absent on the Mac.
- [ ] Fix anything that fails; re-run; commit any manifest fixes.
- [ ] Update spec Status → `implemented`; commit.

### Task 13: Off-box backup

The PVC is the only always-on copy, on a single node, on `local-path`, with `Delete` reclaim and no expansion. The git history the sidecar builds lives on the same disk it is meant to protect, so a node loss takes both. `Prune=false` guards against ArgoCD, not hardware.

**Files:**
- Create: `cluster/agent-knowledge/backup-cronjob.yaml`; add to kustomization.

- [ ] **Step 1: CronJob** — nightly, `alpine/git`, same securityContext and PVC `rw` mount, `git bundle create /backup/obsidian-$(date -u +%F).bundle --all` into a second small PVC, keeping the last 14 and deleting older. A bundle is one file and restores with `git clone`.
- [ ] **Step 2: Pull one bundle to the Mac and restore it into a scratch dir** — an untested backup is not a backup. Verify the restored tree matches `rw/`.
- [ ] **Step 3: Commit.**

### Task 14: Decide the exfiltration posture (write it down, then act on it)

Every containment rule in this design bounds *writes*. Nothing bounds where read content travels once hermes has it, and hermes holds a Telegram channel plus browser CDP against `browserless`. A note containing injected instructions — a web clip, a pasted email, anything ingested — can direct hermes to carry `ro/` content outward. This is a live hole, not a hypothetical, and the spec's "residual risk accepted" line understates it.

- [ ] **Step 1: Pick a posture** and record it in the spec's prompt-injection section: (a) accept, documented, on the grounds that everything in `ro/`+`rw/` is already going to OpenAI anyway; (b) constrain hermes's egress when vault tools are enabled — cluster egress NetworkPolicy on the hermes namespace, allowlisting codex-lb, Telegram, and obsidian-mcp; or (c) split hermes into a vault-reading agent with no browser and a general agent without vault access.
- [ ] **Step 2: Implement whichever was chosen**, or commit the written acceptance if (a).

---

## Self-review notes

- Spec coverage: tiers/sync (T6, T9, T10), MCP tools + containment (T1–T4), admin CLI (T5), lifecycle conventions + hermes instruction block (T11 Steps 4–6), k8s packaging (T7, T8), git history (T8), hardening (T8–T10), durability (T13), verification (T12). Cross-tier link policy is a documentation item — lives in T11 Step 4.
- Phase 1 (hermes over `mac-ssh`) was cut deliberately: hermes must function with the Mac asleep, so the cluster copy is the only agent path. `obsidian-cli` remains an admin tool.
- Known risk: `fastmcp` API drift on `http_app(path=)` — resolved in practice, `http_app(path="/mcp")` works on fastmcp 3.4.4.
- Known risk: NetworkPolicy is new to this cluster; if the CNI does not enforce it (T8 Step 3b), the bearer token is again the sole barrier and T14 becomes more urgent.
- Syncthing pairing state lives in the config PVC, not in git — a PVC loss means re-pairing by hand through the port-forwarded GUI. Record both device IDs somewhere durable.
- `kubectl cp` seeding requires tar in the git-history image (alpine/git has it).
