"""Tests for the CapEx Azure Function deployment package."""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from tools.build_capex_function_package import build_package


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_function_package_contains_runtime_source(tmp_path: Path) -> None:
    output_path = tmp_path / "function.zip"

    metadata = build_package(REPOSITORY_ROOT, output_path)

    with zipfile.ZipFile(output_path) as archive:
        names = set(archive.namelist())

    assert "function_app.py" in names
    assert "host.json" in names
    assert "requirements.txt" in names
    assert "autarch/__init__.py" in names
    assert "autarch/capex/agent_invocation_handler.py" in names
    assert "autarch/capex/agent_invocation_persistence.py" in names
    assert "azure_function/local.settings.json" not in names
    assert not any("__pycache__" in name for name in names)
    assert metadata["sha256"] == hashlib.sha256(
        output_path.read_bytes()
    ).hexdigest().upper()


def test_function_package_is_deterministic(tmp_path: Path) -> None:
    first_path = tmp_path / "first.zip"
    second_path = tmp_path / "second.zip"

    first = build_package(REPOSITORY_ROOT, first_path)
    second = build_package(REPOSITORY_ROOT, second_path)

    assert first["sha256"] == second["sha256"]
    assert first_path.read_bytes() == second_path.read_bytes()
