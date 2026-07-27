import datetime

import pytest

from obsidian_mcp.paths import PathViolation
from obsidian_mcp.tools import ObsidianTools


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
