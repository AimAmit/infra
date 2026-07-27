"""The eight tools hermes sees. Every write target is composed here, never passed in."""
import datetime
import re
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
        # Delimiters mark the body as quoted document content: note text is
        # untrusted input and may carry instructions aimed at the agent.
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

        # Read the ref's mtime instead of shelling out to `git log`. Same
        # answer to "is the history sidecar still committing", without a
        # subprocess or a git binary in the image that serves the notes. The
        # sidecar owns the repo; this container only observes it.
        last_commit = None
        ref = self.root / "rw/.git/refs/heads/main"
        try:
            last_commit = (
                datetime.datetime.fromtimestamp(ref.stat().st_mtime, datetime.UTC)
                .isoformat()
            )
        except OSError:
            pass
        return {"notes_rw": count("rw"), "notes_ro": count("ro"), "last_git_commit": last_commit}
