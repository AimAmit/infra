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
