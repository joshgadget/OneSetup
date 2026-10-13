"""Guard: every source file in the repository must actually be tracked by git.

Found on 2026-10-13: `.gitignore` contained `data/`, which also matched `engine/data/`,
so the entire data-adapter package was missing from the repository for twelve commits
while the working tree kept working. A clone would not have run. This test fails if any
source file is silently ignored.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("engine", "services", "config", "scripts", "tests", "apps/web")
EXTENSIONS = {".py", ".yaml", ".yml", ".ts", ".tsx", ".css", ".html", ".json"}
SKIP_PARTS = {"node_modules", "dist", "__pycache__", ".venv"}


def _tracked_paths() -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def test_every_source_file_is_tracked():
    try:
        tracked = _tracked_paths()
    except Exception as exc:  # git missing, not a checkout: nothing to assert
        pytest.skip(f"git is not usable here: {exc}")

    missing: list[str] = []
    for folder in SOURCE_DIRS:
        base = ROOT / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in EXTENSIONS:
                continue
            if SKIP_PARTS & set(path.parts):
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative not in tracked:
                missing.append(relative)

    assert not missing, "source files are not tracked by git: " + ", ".join(missing)
