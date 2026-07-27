"""Containment: every path argument passes through resolve() before any I/O."""
from pathlib import Path
from stat import S_ISREG

TIERS = ("rw", "ro")


class PathViolation(Exception):
    pass


def resolve(obsidian_root: Path, ref: str) -> Path:
    if not isinstance(ref, str):
        raise PathViolation("path must be a string")
    if "\x00" in ref or "\\" in ref:
        raise PathViolation("forbidden characters in path")
    p = Path(ref)
    if p.is_absolute():
        raise PathViolation("absolute paths not allowed")
    parts = p.parts
    if len(parts) < 2 or parts[0] not in TIERS:
        raise PathViolation(f"path must start with a tier: {TIERS}")
    # Checked against the raw ref, not p.parts: pathlib silently drops trailing
    # and doubled separators, so "rw/x.md/" and "rw//x.md" both yield the clean
    # parts ('rw', 'x.md') and would escape a parts-based check.
    if any(not seg.strip() for seg in ref.split("/")):
        raise PathViolation("empty or whitespace-only path segment")
    if any(part == ".." for part in parts):
        raise PathViolation("path traversal not allowed")
    if any(part.startswith(".") for part in parts[1:]):
        raise PathViolation("dotfiles not allowed")
    if p.suffix != ".md":
        raise PathViolation("only .md files")
    tier_dir = obsidian_root / parts[0]
    # Must precede the .resolve() below: resolving a symlinked tier root yields
    # its target, so the containment check would compare that target against
    # itself and trivially pass. `rw -> ~/Documents` is a plausible setup, not
    # just an attack, and it silently relocates the whole tier.
    if tier_dir.is_symlink():
        raise PathViolation("tier root must not be a symlink")
    tier_root = tier_dir.resolve()
    full = (obsidian_root / p).resolve()  # follows symlinks -> catches escapes
    if not full.is_relative_to(tier_root):
        raise PathViolation("resolved path escapes tier root")
    # A hardlink is a second real directory entry for one inode; is_symlink() is
    # False and resolve() does not undo it, so every check above passes for a
    # note hardlinked out of private/. Nothing distinguishes the links, so any
    # multiply-linked note is refused. Absent files (the create path) have no
    # stat and are unaffected.
    try:
        st = full.stat()
    except OSError:
        st = None
    if st is not None and S_ISREG(st.st_mode) and st.st_nlink > 1:
        raise PathViolation("hardlinked notes not allowed")
    return full
