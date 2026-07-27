import os
import signal
from contextlib import contextmanager

import pytest
from obsidian_mcp.index import search, neighbors, backlinks, _parse_frontmatter
from obsidian_mcp.paths import PathViolation, resolve


@contextmanager
def deadline(seconds=3):
    """Turn a hang into a test failure. A test that can hang the suite forever
    is worse than no test, so every blocking-IO assertion runs under this."""
    def _fire(signum, frame):
        raise TimeoutError(f"blocked for more than {seconds}s")

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


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


# --- Symlink hardening -------------------------------------------------------
# A symlink inside a tier has no legitimate use in this product, and humans
# populate this vault by hand. _iter_notes fails closed: every symlink is
# skipped, so search/backlinks can never read through one.


@pytest.fixture
def symlinked(tmp_path):
    """Vault plus an outside-the-root file, wired up with every symlink shape."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "elsewhere.md").write_text("canary-outside [[People/Alice]]")

    root = tmp_path / "vault"
    (root / "rw").mkdir(parents=True)
    (root / "ro").mkdir()
    (root / "private").mkdir()
    (root / "private/journal.md").write_text("canary-private [[People/Alice]]")
    (root / "rw/real.md").write_text("ordinary rw note")
    (root / "ro/real.md").write_text("ordinary ro note")

    (root / "rw/leak.md").symlink_to(root / "private/journal.md")
    (root / "rw/out.md").symlink_to(outside / "elsewhere.md")
    (root / "rw/alias.md").symlink_to(root / "rw/real.md")
    (root / "rw/sub").symlink_to(root / "private", target_is_directory=True)
    return root


def test_symlink_to_private_file_is_never_read(symlinked):
    hits = search(symlinked, "canary-private")
    assert hits == []
    assert "rw/leak.md" not in backlinks(symlinked, "ro/People/Alice.md")


def test_symlink_outside_vault_root_is_never_read(symlinked):
    hits = search(symlinked, "canary-outside")
    assert hits == []
    assert "rw/out.md" not in backlinks(symlinked, "ro/People/Alice.md")


def test_symlink_within_same_tier_is_also_skipped(symlinked):
    # Intentional over-exclusion: even a symlink whose target is a legitimate
    # in-tier note is skipped. Pinned so the fail-closed rule stays deliberate.
    paths = {h["path"] for h in search(symlinked, "ordinary")}
    assert "rw/alias.md" not in paths


def test_symlinked_directory_into_private_is_not_traversed(symlinked):
    # rglob must not recurse through rw/sub -> private/. Asserted rather than
    # assumed so a Python change to symlink recursion breaks the suite loudly.
    paths = {h["path"] for h in search(symlinked, "canary-private")}
    assert paths == set()
    assert not any(p.startswith("rw/sub") for p in {h["path"] for h in search(symlinked, "")})


def test_regular_notes_in_both_tiers_still_returned(symlinked):
    paths = {h["path"] for h in search(symlinked, "ordinary")}
    assert paths == {"rw/real.md", "ro/real.md"}


# --- Fix round 1: remaining filesystem-aliasing axes ---------------------------
# A tier is a containment boundary, and the filesystem offers four ways to put a
# byte inside it that lives somewhere else: file symlink (covered above),
# hardlink, symlinked tier root, and traversal (covered by paths.resolve).
# These fixtures cover the two that were still open.


def _canary_vault(tmp_path):
    """Vault + an out-of-vault dir, both carrying canaries and a live wikilink."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "elsewhere.md").write_text("canary-outside [[People/Alice]]")
    root = tmp_path / "vault"
    (root / "private").mkdir(parents=True)
    (root / "ro/People").mkdir(parents=True)
    (root / "private/journal.md").write_text("canary-private [[People/Alice]]")
    (root / "ro/People/Alice.md").write_text("# Alice")
    return root, outside


@pytest.fixture
def hardlinked(tmp_path):
    """C1: os.link() makes a second real directory entry - no symlink involved."""
    root, outside = _canary_vault(tmp_path)
    (root / "rw").mkdir()
    (root / "rw/real.md").write_text("ordinary rw note")
    os.link(root / "private/journal.md", root / "rw/hard.md")
    os.link(outside / "elsewhere.md", root / "rw/hardout.md")
    return root


def test_hardlink_to_private_file_is_never_read(hardlinked):
    assert search(hardlinked, "canary-private") == []
    assert "rw/hard.md" not in backlinks(hardlinked, "ro/People/Alice.md")
    with pytest.raises(PathViolation):
        neighbors(hardlinked, "rw/hard.md")


def test_hardlink_to_outside_file_is_never_read(hardlinked):
    assert search(hardlinked, "canary-outside") == []
    assert "rw/hardout.md" not in backlinks(hardlinked, "ro/People/Alice.md")
    with pytest.raises(PathViolation):
        neighbors(hardlinked, "rw/hardout.md")


def test_hardlink_guard_does_not_over_fire(hardlinked):
    # Ordinary single-link notes must still be indexed.
    assert {h["path"] for h in search(hardlinked, "ordinary")} == {"rw/real.md"}


@pytest.fixture
def tier_symlink_private(tmp_path):
    """C2: the tier root itself is the symlink, so every file under it is real."""
    root, _ = _canary_vault(tmp_path)
    (root / "rw").symlink_to(root / "private", target_is_directory=True)
    return root


@pytest.fixture
def tier_symlink_outside(tmp_path):
    """C2 variant: rw/ pointed at a directory entirely outside the vault."""
    root, outside = _canary_vault(tmp_path)
    (root / "rw").symlink_to(outside, target_is_directory=True)
    return root


def test_symlinked_tier_root_into_private_is_skipped(tier_symlink_private):
    assert search(tier_symlink_private, "canary-private") == []
    assert backlinks(tier_symlink_private, "ro/People/Alice.md") == []
    with pytest.raises(PathViolation):
        neighbors(tier_symlink_private, "rw/journal.md")


def test_symlinked_tier_root_outside_vault_is_skipped(tier_symlink_outside):
    assert search(tier_symlink_outside, "canary-outside") == []
    assert backlinks(tier_symlink_outside, "ro/People/Alice.md") == []
    with pytest.raises(PathViolation):
        neighbors(tier_symlink_outside, "rw/elsewhere.md")


def test_private_ref_rejected_by_neighbors_and_backlinks(vault):
    # The central guarantee, exercised through the two resolve()-guarded entry
    # points rather than only through search's tier walk.
    for fn in (neighbors, backlinks):
        with pytest.raises(PathViolation):
            fn(vault, "private/secret.md")


# --- Fix round 1: one bad file must not kill the scan (I3) --------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unreadable_file_does_not_kill_search(vault):
    locked = vault / "rw/locked.md"
    locked.write_text("infra locked")
    locked.chmod(0o000)
    try:
        paths = {h["path"] for h in search(vault, "infra")}
    finally:
        locked.chmod(0o644)
    assert "rw/Daily/2026-07-26.md" in paths
    assert "rw/locked.md" not in paths


def test_directory_named_like_a_note_does_not_kill_search(vault):
    (vault / "rw/notes.md").mkdir()
    paths = {h["path"] for h in search(vault, "infra")}
    assert "rw/Daily/2026-07-26.md" in paths
    assert "rw/notes.md" not in paths


def test_unreadable_file_does_not_kill_backlinks(vault):
    (vault / "rw/notes.md").mkdir()
    assert backlinks(vault, "ro/People/Alice.md") == ["rw/Daily/2026-07-26.md"]


# --- Fix round 1: frontmatter shapes Obsidian actually writes (I4) ------------


def test_frontmatter_block_style_list(vault):
    p = vault / "rw/block.md"
    p.write_text("---\ntags:\n  - daily\n  - work\n---\nbody")
    assert neighbors(vault, "rw/block.md")["frontmatter"]["tags"] == ["daily", "work"]


def test_frontmatter_no_trailing_newline_after_close(vault):
    p = vault / "rw/tight.md"
    p.write_text("---\ntags: [daily]\n---")
    assert neighbors(vault, "rw/tight.md")["frontmatter"]["tags"] == ["daily"]


def test_frontmatter_leading_bom(vault):
    p = vault / "rw/bom.md"
    p.write_text("﻿---\ntags: [daily]\n---\nbody")
    assert neighbors(vault, "rw/bom.md")["frontmatter"]["tags"] == ["daily"]


def test_frontmatter_crlf():
    # Reachable only for direct callers: Path.read_text() applies universal
    # newlines, so neighbors() never sees a \r. Pinned so the parser stays
    # robust if a future caller reads bytes or disables translation.
    fm = _parse_frontmatter("---\r\ntags: [daily]\r\n---\r\nbody")
    assert fm["tags"] == ["daily"]


def test_frontmatter_scalars_and_inline_list_still_work(vault):
    p = vault / "rw/mixed.md"
    p.write_text("---\ntitle: Standup\ntags: [a, b]\nempty:\n---\nbody")
    fm = neighbors(vault, "rw/mixed.md")["frontmatter"]
    assert fm["title"] == "Standup"
    assert fm["tags"] == ["a", "b"]
    assert fm["empty"] == ""


# --- Fix round 2: non-regular files reaching resolve() (I5) -------------------
# _iter_notes filters on S_ISREG, but resolve() only used S_ISREG to gate the
# nlink check, so a non-regular file passed containment untouched. search and
# backlinks were fine (they never see it); neighbors opened it.


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs only")
def test_fifo_named_like_a_note_is_rejected_by_resolve(vault):
    os.mkfifo(vault / "rw/fifo.md")
    with pytest.raises(PathViolation):
        resolve(vault, "rw/fifo.md")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs only")
def test_fifo_does_not_hang_neighbors(vault):
    # Opening a FIFO with no writer blocks forever in open(): no exception, no
    # timeout, so Task 4's "catch OSError" guidance cannot help. The deadline
    # converts a regression from an infinite hang into a visible failure.
    os.mkfifo(vault / "rw/fifo.md")
    with deadline(3):
        with pytest.raises(PathViolation):
            neighbors(vault, "rw/fifo.md")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs only")
def test_fifo_is_invisible_to_the_walkers(vault):
    # Already true via _iter_notes' S_ISREG filter - pinned so the two layers
    # cannot drift apart again.
    os.mkfifo(vault / "rw/fifo.md")
    with deadline(3):
        assert "rw/fifo.md" not in {h["path"] for h in search(vault, "")}
        assert "rw/fifo.md" not in backlinks(vault, "ro/People/Alice.md")


def test_directory_named_like_a_note_raises_path_violation(vault):
    # Was IsADirectoryError, whose message carries the absolute vault path.
    # Now a path-free PathViolation, shrinking what Task 4 has to scrub.
    (vault / "rw/notes.md").mkdir()
    with pytest.raises(PathViolation):
        resolve(vault, "rw/notes.md")
    with pytest.raises(PathViolation):
        neighbors(vault, "rw/notes.md")


def test_resolve_still_allows_not_yet_existing_notes(vault):
    # The create path: writes.py resolves before the file exists. The
    # `st is not None` guard is what keeps this working - do not regress it.
    assert resolve(vault, "rw/new.md") == vault / "rw/new.md"
    deep = resolve(vault, "rw/Nested/Deeper/new.md")
    assert deep == vault / "rw/Nested/Deeper/new.md"
    assert not deep.parent.exists()  # parent absent too, still fine


def test_symlinked_ro_tier_root_is_also_skipped(tmp_path):
    # TIERS is iterated uniformly, but the round-1 tests only ever pinned rw/.
    # The re-reviewer exercised ro -> private/ by hand; pin it so the symmetry
    # is enforced rather than assumed.
    root, _ = _canary_vault(tmp_path)
    (root / "ro").rename(root / "ro-real")
    (root / "rw").mkdir()
    (root / "ro").symlink_to(root / "private", target_is_directory=True)
    assert search(root, "canary-private") == []
    assert backlinks(root, "rw/anything.md") == []
    with pytest.raises(PathViolation):
        neighbors(root, "ro/journal.md")
