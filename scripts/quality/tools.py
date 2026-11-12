"""Run fixed real-tool commands and retain their exit codes and outputs."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .debt import JsonValue

PINNED_TOOLS: dict[str, str] = {"ruff": "0.17.4", "mypy": "2.4.1", "import-linter": "2.16"}
PRODUCTION_ROOTS: tuple[str, ...] = ("pythonstan", "pythonstan_inspector")


@dataclass(frozen=True)
class ToolRun:
    name: str
    command: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    seconds: float

    def record(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "command": list(self.command),
            "exit_code": self.exit_code,
            "seconds": self.seconds,
        }


def run_tool(root: Path, output: Path, name: str, command: tuple[str, ...]) -> ToolRun:
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    elapsed = time.perf_counter() - started
    (output / f"{name}.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (output / f"{name}.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    return ToolRun(name, command, completed.returncode, completed.stdout, completed.stderr, elapsed)


def tool_versions() -> dict[str, str]:
    versions = {name: importlib.metadata.version(name) for name in PINNED_TOOLS}
    if versions != PINNED_TOOLS:
        raise ValueError(f"Tool versions differ from reviewed pins: {versions}")
    return versions


def production_files(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for name in PRODUCTION_ROOTS
            for path in (root / name).rglob("*.py")
        )
    )


def collect_tools(root: Path, output: Path, paths: tuple[str, ...]) -> tuple[ToolRun, ...]:
    python = sys.executable
    commands = (
        ("format", (python, "-m", "ruff", "format", "--check", "--output-format=json", *paths)),
        ("ruff", (python, "-m", "ruff", "check", "--output-format=json", *paths)),
        ("mypy", (python, "-m", "mypy", "--no-incremental", "--output=json", *paths)),
        ("imports", (str(Path(python).parent / "lint-imports"), "--no-cache")),
        ("ruff-settings", (python, "-m", "ruff", "check", "--show-settings", paths[0])),
    )
    return tuple(run_tool(root, output, name, command) for name, command in commands)
