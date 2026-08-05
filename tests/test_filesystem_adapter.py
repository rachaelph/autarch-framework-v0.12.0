"""FileSystemAdapter tests — confinement and reversibility."""
import pytest

from autarch.adapters.filesystem import FileSystemAdapter
from autarch.contracts import Action


def test_write_then_read(tmp_path):
    adapter = FileSystemAdapter(root=tmp_path)
    write = adapter.execute(Action("file.write", {"path": "a.txt", "content": "hello"}))
    assert write.ok is True
    read = adapter.execute(Action("file.read", {"path": "a.txt"}))
    assert read.ok is True
    assert read.output == "hello"


def test_write_captures_undo_for_new_file(tmp_path):
    adapter = FileSystemAdapter(root=tmp_path)
    result = adapter.execute(Action("file.write", {"path": "a.txt", "content": "hi"}))
    assert result.undo is not None
    # New file -> undo is a deletion (restore == None).
    assert result.undo["capability"] == "file.delete"
    assert result.undo["restore"] is None


def test_overwrite_captures_prior_content(tmp_path):
    adapter = FileSystemAdapter(root=tmp_path)
    adapter.execute(Action("file.write", {"path": "a.txt", "content": "first"}))
    result = adapter.execute(Action("file.write", {"path": "a.txt", "content": "second"}))
    assert result.undo["capability"] == "file.write"
    assert result.undo["restore"] == "first"


def test_delete_captures_undo(tmp_path):
    adapter = FileSystemAdapter(root=tmp_path)
    adapter.execute(Action("file.write", {"path": "a.txt", "content": "keep"}))
    result = adapter.execute(Action("file.delete", {"path": "a.txt"}))
    assert result.ok is True
    assert result.undo["restore"] == "keep"


def test_move(tmp_path):
    adapter = FileSystemAdapter(root=tmp_path)
    adapter.execute(Action("file.write", {"path": "a.txt", "content": "x"}))
    result = adapter.execute(Action("file.move", {"path": "a.txt", "dest": "b.txt"}))
    assert result.ok is True
    assert adapter.execute(Action("file.read", {"path": "b.txt"})).output == "x"


def test_sandbox_escape_blocked(tmp_path):
    adapter = FileSystemAdapter(root=tmp_path)
    result = adapter.execute(Action("file.write", {"path": "../escape.txt", "content": "x"}))
    assert result.ok is False
    assert "escapes sandbox" in result.error


def test_read_missing_file(tmp_path):
    adapter = FileSystemAdapter(root=tmp_path)
    result = adapter.execute(Action("file.read", {"path": "nope.txt"}))
    assert result.ok is False
    assert "not found" in result.error
