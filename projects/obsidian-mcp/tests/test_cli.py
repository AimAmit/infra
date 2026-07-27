import json

from obsidian_mcp.cli import run


def test_cli_read(tmp_path, capsys):
    (tmp_path / "rw").mkdir()
    (tmp_path / "ro").mkdir()
    (tmp_path / "ro/a.md").write_text("hi")
    assert run(["--root", str(tmp_path), "read", "ro/a.md"]) == 0
    assert "hi" in capsys.readouterr().out


def test_cli_violation_exit_1(tmp_path, capsys):
    (tmp_path / "rw").mkdir()
    (tmp_path / "ro").mkdir()
    assert run(["--root", str(tmp_path), "read", "private/x.md"]) == 1
    assert "error" in json.loads(capsys.readouterr().out)


def test_cli_capture(tmp_path, capsys):
    (tmp_path / "rw").mkdir()
    (tmp_path / "ro").mkdir()
    assert run(["--root", str(tmp_path), "capture", "T", "body", "--tags", "a,b"]) == 0
    ref = json.loads(capsys.readouterr().out)["ref"]
    assert (tmp_path / ref).exists()
