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
