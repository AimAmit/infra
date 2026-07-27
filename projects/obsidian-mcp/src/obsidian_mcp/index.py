"""Markdown link graph. Full rescan per call - personal vault, thousands of files max."""
import re
from pathlib import Path
from stat import S_ISREG

from .paths import TIERS, resolve

WIKILINK = re.compile(r"\[\[([^\]|#]+)")
TAG = re.compile(r"(?<!\S)#([A-Za-z][\w/-]*)")
# Tolerates a leading BOM, CRLF, and a closing fence at EOF with no trailing
# newline - all shapes real editors produce, all of which used to yield {}.
FRONTMATTER = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def _iter_notes(obsidian_root: Path):
    for tier in TIERS:
        root = obsidian_root / tier
        # A tier root that is itself a symlink defeats every per-file check
        # below: is_dir() follows it and the files underneath are genuinely
        # real, so nothing downstream can tell that `rw/` is actually
        # private/ or ~/Documents. Skip the whole tier.
        if root.is_symlink() or not root.is_dir():
            continue
        for p in sorted(root.rglob("*.md")):
            rel = p.relative_to(obsidian_root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            # Fail closed on symlinks. resolve() guards neighbors(), but search
            # and backlinks walk the tree directly, so a hand-made link like
            # rw/notes.md -> private/journal.md would pipe private content into
            # results. A symlink inside a tier has no legitimate use here, so
            # skip every one rather than resolving and comparing to the tier
            # root - less logic, and nothing to get subtly wrong.
            if p.is_symlink():
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            # rglob("*.md") also matches directories named like notes, and a
            # broken entry would raise mid-scan and kill the whole search.
            if not S_ISREG(st.st_mode):
                continue
            # A hardlink is a second real directory entry for the same inode:
            # is_symlink() is False and Path.resolve() does not undo it, so
            # containment cannot see it. We cannot tell which link is "the
            # original", so treat any multiply-linked note as suspect - in a
            # notes vault that has no legitimate use, and over-exclusion is
            # the right direction to fail.
            if st.st_nlink > 1:
                continue
            yield str(rel), p


def _read(p: Path) -> str | None:
    """Text, or None when unreadable - one bad file must not kill a whole scan."""
    try:
        return p.read_text(errors="ignore")
    except OSError:
        return None


def _parse_frontmatter(text: str) -> dict:
    """A deliberate YAML subset: scalars, inline lists, and block lists.

    Not a YAML parser. Nested mappings, folded/literal scalars, quoting, and
    anchors are out of scope - see the task report for the documented limits.
    """
    m = FRONTMATTER.match(text)
    if not m:
        return {}
    out = {}
    key = None  # last scalar key seen, so a following "- item" can attach to it
    for line in m.group(1).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "-" or stripped.startswith("- "):
            # Block-style list item. This is what Obsidian writes when tags are
            # added through its UI, so it is the common case, not the exotic one.
            item = stripped[1:].strip()
            if key is None or not item:
                continue
            if not isinstance(out.get(key), list):
                out[key] = []  # replaces the "" placeholder left by "tags:"
            out[key].append(item)
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            out[k] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
            key = None
        else:
            out[k] = v
            key = k if v == "" else None
    return out


def search(obsidian_root: Path, query: str, limit: int = 20) -> list[dict]:
    q = query.lower()
    hits = []
    for rel, p in _iter_notes(obsidian_root):
        text = _read(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
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
        text = _read(p)
        if text is None:
            continue
        for link in WIKILINK.findall(text):
            if link.strip() in (tierless, stem):
                out.append(rel)
                break
    return out
