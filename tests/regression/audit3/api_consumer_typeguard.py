"""Exercise the real Session constructor signature with normal mypy imports."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import TypedDict


class ConsumerCheck(TypedDict):
    status: str
    consumer_diagnostics: int
    transitive_error_lines: int
    tool_exit_code: int


def _consumer_error(line: str, *, path: Path, repository: Path) -> bool:
    match = re.match(r"^(.+?):\d+(?::\d+)?: error:", line)
    if match is None:
        return False
    source = Path(match.group(1))
    if not source.is_absolute():
        source = repository / source
    return source.resolve() == path.resolve()


def check_consumers(*, repository: Path, workspace: Path, python: str) -> dict[str, ConsumerCheck]:
    workspace.mkdir(parents=True, exist_ok=True)
    fixtures = {
        "positive": 'from pythonstan import AnalysisSession, Source, resolve_request\nsession = AnalysisSession(resolve_request(Source("x=1")))\n',
        "negative": 'from pythonstan import AnalysisSession\nsession = AnalysisSession({"output": {"views": ["values"]}})\n',
    }
    results: dict[str, ConsumerCheck] = {}
    commands: list[dict[str, object]] = []
    for name, source in fixtures.items():
        path = workspace / (name + ".py")
        path.write_text(source)
        command = [
            python,
            "-m",
            "mypy",
            "--strict",
            "--cache-dir",
            str(workspace / "mypy-cache"),
            str(path),
        ]
        completed = subprocess.run(
            command, cwd=repository, capture_output=True, text=True, timeout=120, check=False
        )
        (workspace / (name + ".stdout.txt")).write_text(completed.stdout)
        (workspace / (name + ".stderr.txt")).write_text(completed.stderr)
        owned = [
            line
            for line in completed.stdout.splitlines()
            if _consumer_error(line, path=path, repository=repository)
        ]
        transitive = [
            line
            for line in completed.stdout.splitlines()
            if "error:" in line and not _consumer_error(line, path=path, repository=repository)
        ]
        if name == "positive":
            passed = not owned and completed.returncode in {0, 1}
        else:
            passed = (
                completed.returncode == 1
                and len(owned) == 1
                and "[arg-type]" in owned[0]
                and "ResolvedRequest" in owned[0]
            )
        results[name] = {
            "status": "passed" if passed else "failed",
            "consumer_diagnostics": len(owned),
            "transitive_error_lines": len(transitive),
            "tool_exit_code": completed.returncode,
        }
        commands.append({"command": command, "exit_code": completed.returncode, "consumer": name})
    sources = ["pythonstan/session.py", "pythonstan/request.py", "pythonstan/api.py"]
    receipt = {
        "scope": "Actual consumer signature only; all transitive diagnostics retained and not declared clean",
        "source_sha256": {
            name: hashlib.sha256((repository / name).read_bytes()).hexdigest() for name in sources
        },
        "commands": commands,
        "results": results,
    }
    (workspace / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return results
