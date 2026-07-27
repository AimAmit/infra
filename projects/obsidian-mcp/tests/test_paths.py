import os
import pytest
from pathlib import Path
from obsidian_mcp.paths import resolve, PathViolation


@pytest.fixture
def vault(tmp_path):
    for t in ("rw", "ro", "private"):
        (tmp_path / t).mkdir()
    return tmp_path


def test_valid_rw_path(vault):
    p = resolve(vault, "rw/Daily/2026-07-26.md")
    assert p == (vault / "rw/Daily/2026-07-26.md").resolve()

def test_private_tier_rejected(vault):
    with pytest.raises(PathViolation):
        resolve(vault, "private/Journal/x.md")

def test_traversal_rejected(vault):
    with pytest.raises(PathViolation):
        resolve(vault, "rw/../private/x.md")

def test_absolute_rejected(vault):
    with pytest.raises(PathViolation):
        resolve(vault, "/etc/passwd")

def test_dotfile_rejected(vault):
    with pytest.raises(PathViolation):
        resolve(vault, "rw/.obsidian/app.json")

def test_non_md_rejected(vault):
    with pytest.raises(PathViolation):
        resolve(vault, "rw/script.sh")

def test_null_byte_rejected(vault):
    with pytest.raises(PathViolation):
        resolve(vault, "rw/a\x00b.md")

def test_symlink_escape_rejected(vault):
    (vault / "private/secret.md").write_text("s")
    os.symlink(vault / "private", vault / "rw/leak")
    with pytest.raises(PathViolation):
        resolve(vault, "rw/leak/secret.md")


# --- fix round 1 ---

def test_valid_ro_path(vault):
    p = resolve(vault, "ro/Refs/paper.md")
    assert p == (vault / "ro/Refs/paper.md").resolve()

@pytest.mark.parametrize("ref", [Path("rw/a.md"), None, b"rw/a.md", 42])
def test_non_str_ref_rejected(vault, ref):
    with pytest.raises(PathViolation):
        resolve(vault, ref)

@pytest.mark.parametrize("ref", ["rw/x.md/", "rw//x.md", "rw/ /x.md", "rw/\t/x.md"])
def test_empty_or_whitespace_segment_rejected(vault, ref):
    with pytest.raises(PathViolation):
        resolve(vault, ref)
