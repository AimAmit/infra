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
