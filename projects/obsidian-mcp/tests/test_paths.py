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
