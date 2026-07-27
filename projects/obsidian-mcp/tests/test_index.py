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
