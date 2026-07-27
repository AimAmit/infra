import pytest
from obsidian_mcp.writes import Writer, WriteLimit, MAX_PER_HOUR


def test_creates_file(tmp_path):
    out = Writer().create(tmp_path / "a.md", "hello")
    assert out.read_text() == "hello"

def test_never_overwrites(tmp_path):
    w = Writer()
    first = w.create(tmp_path / "a.md", "one")
    second = w.create(tmp_path / "a.md", "two")
    assert second != first
    assert second.name == "a-2.md"
    assert first.read_text() == "one"

def test_size_cap(tmp_path):
    with pytest.raises(WriteLimit):
        Writer().create(tmp_path / "big.md", "x" * 70000)

def test_rate_cap(tmp_path):
    t = [0.0]
    w = Writer(clock=lambda: t[0])
    for i in range(MAX_PER_HOUR):
        w.create(tmp_path / f"n{i}.md", "ok")
    with pytest.raises(WriteLimit):
        w.create(tmp_path / "over.md", "no")
    t[0] = 3601.0  # window rolls over
    w.create(tmp_path / "later.md", "ok")
