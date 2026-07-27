"""obsidian_status reports history freshness without a git binary or a subprocess."""
import datetime
import os

from obsidian_mcp.tools import ObsidianTools


def _root(tmp_path):
    (tmp_path / "rw").mkdir()
    (tmp_path / "ro").mkdir()
    return tmp_path


def test_no_history_yet(tmp_path):
    s = ObsidianTools(_root(tmp_path)).obsidian_status()
    assert s["last_git_commit"] is None


def test_reports_ref_mtime(tmp_path):
    root = _root(tmp_path)
    ref = root / "rw/.git/refs/heads/main"
    ref.parent.mkdir(parents=True)
    ref.write_text("0" * 40 + "\n")
    os.utime(ref, (1_800_000_000, 1_800_000_000))
    s = ObsidianTools(root).obsidian_status()
    expected = datetime.datetime.fromtimestamp(1_800_000_000, datetime.UTC).isoformat()
    assert s["last_git_commit"] == expected


def test_counts_exclude_nothing_visible(tmp_path):
    root = _root(tmp_path)
    (root / "rw/a.md").write_text("x")
    (root / "ro/b.md").write_text("y")
    s = ObsidianTools(root).obsidian_status()
    assert s["notes_rw"] == 1 and s["notes_ro"] == 1
