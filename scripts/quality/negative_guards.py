"""Exercise quality tools with controlled positive and deliberately broken inputs.

These fixtures validate the tools and guard orchestration. They do not establish
that equivalent contracts have already been adopted by the production analyzer.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import textwrap
import zipfile
from collections.abc import Collection
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class GuardResult:
    """One invocation and the independently expected acceptance decision."""

    name: str
    command: tuple[str, ...]
    expected_exit: int
    actual_exit: int
    passed: bool
    diagnostic_path: str


@dataclass(frozen=True)
class GuardReport:
    """Tool-effectiveness results, distinct from production type qualification."""

    checks: tuple[GuardResult, ...]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)


def validate_distribution(
    members: Collection[str], required: Collection[str], retired: Collection[str]
) -> tuple[str, ...]:
    """Reject missing public members and any retired module in an archive."""

    present = frozenset(members)
    errors = [f"missing:{member}" for member in sorted(set(required) - present)]
    errors.extend(f"retired:{member}" for member in sorted(set(retired) & present))
    return tuple(errors)


_CONTRACTS = """
from dataclasses import dataclass
from typing import NewType, assert_never

ObjectId = NewType("ObjectId", str)
CellId = NewType("CellId", str)

@dataclass(frozen=True)
class ResolvedRequest:
    output: str

class Session:
    def __init__(self, request: ResolvedRequest) -> None:
        self.request: ResolvedRequest = request

@dataclass(frozen=True)
class Normal:
    value: int

@dataclass(frozen=True)
class Raised:
    reason: str

type Flow = Normal | Raised

def consume_flow(flow: Flow) -> int:
    match flow:
        case Normal(value):
            return value
        case Raised():
            return 0
        case _:
            assert_never(flow)

@dataclass(frozen=True)
class _CheckerSeal:
    pass

@dataclass(frozen=True)
class Candidate:
    value: int

@dataclass(frozen=True)
class CheckedSummary:
    seal: _CheckerSeal
    value: int

def check_summary(value: int) -> CheckedSummary:
    return CheckedSummary(_CheckerSeal(), value)

def apply_summary(summary: CheckedSummary) -> int:
    return summary.value

def read_object(identity: ObjectId) -> str:
    return identity
"""

_IMPORT_CONFIG = """
[importlinter]
root_package = guard_package

[importlinter:contract:core]
name = Core cannot import providers
type = forbidden
source_modules =
    guard_package.core
forbidden_modules =
    guard_package.provider
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")


def _invoke(
    name: str,
    command: tuple[str, ...],
    directory: Path,
    expected_exit: int,
    required_diagnostic: str = "",
) -> GuardResult:
    process = subprocess.run(
        command, cwd=directory, capture_output=True, text=True, timeout=60, check=False
    )
    diagnostic = process.stdout + process.stderr
    path = directory / "diagnostic.txt"
    path.write_text(diagnostic, encoding="utf-8")
    return GuardResult(
        name=name,
        command=command,
        expected_exit=expected_exit,
        actual_exit=process.returncode,
        passed=process.returncode == expected_exit and required_diagnostic in diagnostic,
        diagnostic_path=str(path),
    )


def _type_guard(
    python: Path,
    output_dir: Path,
    name: str,
    consumer: str,
    *,
    expected_exit: int = 1,
    diagnostic: str = "[arg-type]",
    contracts: str = _CONTRACTS,
    exports: str = "from .contracts import ObjectId as ObjectId\n",
) -> GuardResult:
    directory = output_dir / name
    _write(directory / "guard_package" / "contracts.py", contracts)
    _write(directory / "guard_package" / "__init__.py", exports)
    _write(directory / "consumer.py", consumer)
    config_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    command = (
        str(python),
        "-m",
        "mypy",
        "--config-file",
        str(config_path),
        "--no-incremental",
        "guard_package",
        "consumer.py",
    )
    return _invoke(name, command, directory, expected_exit, diagnostic)


def _import_guard(python: Path, output_dir: Path, *, broken: bool) -> GuardResult:
    name = "reverse_import" if broken else "import_positive"
    directory = output_dir / name
    _write(directory / "guard_package" / "__init__.py", "")
    core = "from . import provider\n" if broken else "answer: int = 42\n"
    _write(directory / "guard_package" / "core.py", core)
    _write(directory / "guard_package" / "provider.py", "answer: int = 42\n")
    _write(directory / ".importlinter", _IMPORT_CONFIG)
    command = (str(python.parent / "lint-imports"), "--config", ".importlinter", "--no-cache")
    return _invoke(name, command, directory, int(broken), "1 broken" if broken else "1 kept")


def _ruff_guard(python: Path, output_dir: Path, *, broken: bool) -> GuardResult:
    name = "ruff_annotation" if broken else "ruff_positive"
    directory = output_dir / name
    source = "def result():\n    return 1\n" if broken else "def result() -> int:\n    return 1\n"
    _write(directory / "fixture.py", source)
    config_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    command = (str(python), "-m", "ruff", "check", "--config", str(config_path), "fixture.py")
    return _invoke(
        name, command, directory, int(broken), "ANN201" if broken else "All checks passed"
    )


def _distribution_guards(output_dir: Path) -> tuple[GuardResult, ...]:
    required = ("guard_package/__init__.py", "guard_package/py.typed")
    retired = ("guard_package/retired_prototype.py",)
    variants = {
        "wheel_positive": (required, ()),
        "retired_wheel": ((*required, *retired), (f"retired:{retired[0]}",)),
        "missing_py_typed": ((required[0],), (f"missing:{required[1]}",)),
    }
    results: list[GuardResult] = []
    for name, (members, expected) in variants.items():
        directory = output_dir / name
        directory.mkdir(parents=True, exist_ok=True)
        archive = directory / "guard.whl"
        with zipfile.ZipFile(archive, "w") as wheel:
            for member in members:
                wheel.writestr(member, "")
        with zipfile.ZipFile(archive) as wheel:
            errors = validate_distribution(wheel.namelist(), required, retired)
        path = directory / "diagnostic.txt"
        path.write_text("\n".join(errors), encoding="utf-8")
        actual = int(bool(errors))
        results.append(
            GuardResult(
                name,
                ("validate_distribution", str(archive)),
                int(bool(expected)),
                actual,
                errors == expected,
                str(path),
            )
        )
    return tuple(results)


def run_guards(python: Path, output_dir: Path) -> GuardReport:
    """Run real mypy/import-linter and archive guards; retain every diagnostic."""

    # Resolving the venv interpreter symlink would silently select the base environment.
    python = python.absolute()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    checks = [
        _type_guard(
            python,
            output_dir,
            "types_positive",
            """
            from guard_package import ObjectId
            from guard_package.contracts import (
                Normal, ResolvedRequest, Session, check_summary,
                apply_summary, consume_flow, read_object,
            )
            session = Session(ResolvedRequest("json"))
            identity = read_object(ObjectId("object"))
            result = consume_flow(Normal(1)) + apply_summary(check_summary(2))
            """,
            expected_exit=0,
            diagnostic="Success:",
        ),
        _type_guard(
            python,
            output_dir,
            "identity_swap",
            "from guard_package.contracts import CellId, read_object\n"
            "read_object(CellId('cell'))\n",
        ),
        _type_guard(
            python,
            output_dir,
            "raw_request",
            "from guard_package.contracts import Session\nSession({'output': 'json'})\n",
        ),
        _type_guard(
            python,
            output_dir,
            "flow_variant",
            "from guard_package.contracts import Normal, consume_flow\nconsume_flow(Normal(1))\n",
            contracts=_CONTRACTS.replace(
                "type Flow = Normal | Raised",
                "@dataclass(frozen=True)\nclass Suspended:\n    token: str\n\n"
                "type Flow = Normal | Raised | Suspended",
            ),
        ),
        _type_guard(
            python,
            output_dir,
            "core_any",
            "from typing import Any\nvalue: Any = 1\n",
            diagnostic="[explicit-any]",
        ),
        _type_guard(
            python,
            output_dir,
            "unchecked_summary",
            "from guard_package.contracts import Candidate, CheckedSummary\n"
            "CheckedSummary(Candidate(3), 3)\n",
            diagnostic="[arg-type]",
        ),
        _type_guard(
            python,
            output_dir,
            "missing_reexport",
            "from guard_package import ObjectId\nidentity = ObjectId('object')\n",
            exports="from .contracts import ObjectId\n",
            diagnostic="[attr-defined]",
        ),
        _import_guard(python, output_dir, broken=False),
        _import_guard(python, output_dir, broken=True),
        _ruff_guard(python, output_dir, broken=False),
        _ruff_guard(python, output_dir, broken=True),
        *_distribution_guards(output_dir),
    ]
    report = GuardReport(tuple(checks))
    (output_dir / "report.json").write_text(
        json.dumps({"passed": report.passed, "checks": [asdict(c) for c in checks]}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    arguments = parser.parse_args()
    report = run_guards(arguments.python, arguments.output_dir)
    print(json.dumps({"passed": report.passed, "checks": len(report.checks)}))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
