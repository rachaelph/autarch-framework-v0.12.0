"""Build the deployable CapEx Azure Function ZIP package."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Iterable


FUNCTION_FILES = (
    ("azure_function/function_app.py", "function_app.py"),
    ("azure_function/host.json", "host.json"),
    ("azure_function/requirements.txt", "requirements.txt"),
)
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _tracked_autarch_files(repository_root: Path) -> Iterable[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--", "autarch"],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )

    for line in sorted(result.stdout.splitlines()):
        relative_path = Path(line)
        if (
            relative_path.suffix == ".pyc"
            or "__pycache__" in relative_path.parts
        ):
            continue
        yield relative_path


def _write_file(
    archive: zipfile.ZipFile,
    source_path: Path,
    archive_name: str,
) -> None:
    info = zipfile.ZipInfo(
        archive_name.replace("\\", "/"),
        date_time=FIXED_ZIP_TIMESTAMP,
    )
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, source_path.read_bytes())


def build_package(
    repository_root: Path,
    output_path: Path,
) -> dict[str, object]:
    repository_root = repository_root.resolve()
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    package_files = [
        (repository_root / source, archive_name)
        for source, archive_name in FUNCTION_FILES
    ]
    package_files.extend(
        (repository_root / path, path.as_posix())
        for path in _tracked_autarch_files(repository_root)
    )

    missing_files = [
        str(source_path)
        for source_path, _ in package_files
        if not source_path.is_file()
    ]
    if missing_files:
        raise FileNotFoundError(
            "Function package inputs are missing: "
            + ", ".join(missing_files)
        )

    with zipfile.ZipFile(
        output_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for source_path, archive_name in sorted(
            package_files,
            key=lambda item: item[1],
        ):
            _write_file(archive, source_path, archive_name)

    digest = hashlib.sha256(output_path.read_bytes()).hexdigest().upper()

    return {
        "outputPath": str(output_path),
        "sha256": digest,
        "fileCount": len(package_files),
        "sizeBytes": output_path.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the CapEx Function deployment ZIP.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "dist/capex-invoice-agent-invocation.zip"
        ),
    )
    args = parser.parse_args()

    repository_root = Path(__file__).resolve().parents[1]
    metadata = build_package(repository_root, args.output)
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
