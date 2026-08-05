"""Substrate tests — the portable host abstraction."""
from pathlib import Path

from autarch.substrate import Substrate


def test_detect_returns_populated_fields():
    sub = Substrate.detect()
    assert sub.os_name
    assert sub.machine
    assert sub.python.count(".") >= 1


def test_tags_include_form_factor():
    sub = Substrate.detect()
    tags = sub.tags
    assert ("mobile" in tags) or ("desktop" in tags)
    assert sub.os_name.lower() in tags


def test_data_dir_is_under_app():
    sub = Substrate.detect()
    path = sub.data_dir("autarch")
    assert isinstance(path, Path)
    assert path.name == "autarch"


def test_describe_is_human_readable():
    text = Substrate.detect().describe()
    assert "Python" in text
