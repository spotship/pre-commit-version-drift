"""Shared fixtures.

Every test drives the real CLI against a throwaway git repo. The CLI contract --
exit code, stderr text, file:line reporting -- is what consumers actually
experience; asserting on the RULES tuple would test something nobody runs.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from version_drift.cli import main

# A synthetic fixture version, deliberately NOT this repo's own .python-version.
# Every expectation here is derived from it -- "3.12", "312", the ">=3.13" floor
# that has to sit above it -- so binding it to the repo would red-light most of
# the suite on the next minor bump rather than self-update.
DECLARED = "3.12.13"


@dataclass(frozen=True)
class CliResult:
    code: int
    out: str
    err: str


@pytest.fixture
def make_repo(tmp_path):
    """Create a git repo with `files` staged. `version=None` omits the source file."""

    def _make(
        files: dict[str, str],
        version: str | None = DECLARED,
        source: str = ".python-version",
    ) -> Path:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
        if version is not None:
            (tmp_path / source).write_text(f"{version}\n", encoding="utf-8")
        for rel, content in files.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        return tmp_path

    return _make


@pytest.fixture
def run_cli(capsys):
    def _run(repo: Path, *args: str) -> CliResult:
        code = main(["--root", str(repo), *args])
        captured = capsys.readouterr()
        return CliResult(code, captured.out, captured.err)

    return _run
