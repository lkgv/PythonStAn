"""Reject disabled scans, missing contracts, debt resets and frontier retreat."""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import tokenize
import tomllib
from collections.abc import Mapping
from pathlib import Path

from .debt import JsonValue, json_value, mapping, module_id, sequence, string

REQUIRED_RUFF: frozenset[str] = frozenset(
    {
        "E4",
        "E7",
        "E9",
        "F",
        "I",
        "UP",
        "B",
        "C4",
        "PIE",
        "RET",
        "SIM",
        "ANN",
        "RUF100",
        "PGH003",
        "PGH004",
    }
)
REQUIRED_MYPY_CODES: frozenset[str] = frozenset(
    {
        "ignore-without-code",
        "explicit-override",
        "exhaustive-match",
        "mutable-override",
        "possibly-undefined",
        "unused-awaitable",
    }
)


def configured_contracts(root: Path, paths: tuple[str, ...]) -> int:
    raw: object = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    config = mapping(json_value(raw))
    tool = mapping(config["tool"])
    mypy = mapping(tool["mypy"])
    for option in (
        "strict",
        "warn_unreachable",
        "warn_unused_configs",
        "disallow_any_unimported",
        "disallow_any_explicit",
    ):
        if mypy.get(option) is not True:
            raise ValueError(f"Required mypy setting is disabled: {option}")
    if mypy.get("files") != ["pythonstan", "pythonstan_inspector"]:
        raise ValueError("mypy production roots changed")
    codes = {string(value) for value in sequence(mypy["enable_error_code"])}
    if not codes >= REQUIRED_MYPY_CODES:
        raise ValueError("Required mypy error codes disabled")
    modules = {module_id(path) for path in paths}
    overrides = [mapping(item) for item in sequence(mypy.get("overrides", []))]
    for options in [mypy, *overrides]:
        if any(options.get(name) is True for name in ("ignore_errors", "ignore_missing_imports")):
            raise ValueError("Blanket mypy ignore is forbidden")
        if options.get("follow_imports") == "skip" or options.get("exclude"):
            raise ValueError("mypy scan exclusion is forbidden")
        if options.get("disable_error_code"):
            raise ValueError("mypy error-code disabling is forbidden")
    for option in (
        "disallow_any_generics",
        "disallow_subclassing_any",
        "disallow_untyped_calls",
        "disallow_untyped_defs",
        "disallow_incomplete_defs",
        "check_untyped_defs",
        "disallow_untyped_decorators",
        "warn_redundant_casts",
        "warn_unused_ignores",
        "warn_return_any",
        "strict_equality",
        "strict_bytes",
        "extra_checks",
    ):
        if mypy.get(option) is False:
            raise ValueError(f"mypy strictness weakened: {option}")
    if mypy.get("implicit_reexport") is True:
        raise ValueError("mypy implicit re-export weakens the public boundary")
    for override in overrides:
        if set(override) - {"module", "disallow_any_expr"}:
            raise ValueError("mypy overrides may only add the reviewed Any-expression restriction")
        if override.get("disallow_any_expr") is not True:
            raise ValueError("mypy override cannot weaken expression checking")
        for pattern in sequence(override["module"]):
            text = string(pattern)
            if not any(fnmatch.fnmatchcase(module, text) for module in modules):
                raise ValueError(f"Unmatched mypy override: {text}")
    ruff = mapping(tool["ruff"])
    lint = mapping(ruff["lint"])
    selected = {string(value) for value in sequence(lint["select"])}
    ignored = any(
        lint.get(name)
        for name in ("ignore", "extend-ignore", "per-file-ignores", "extend-per-file-ignores")
    )
    if not selected >= REQUIRED_RUFF or ignored or ruff.get("force-exclude") is True:
        raise ValueError("Required Ruff rules disabled or ignored")
    linter = mapping(tool["importlinter"])
    if (
        linter.get("root_packages") != ["pythonstan", "pythonstan_inspector"]
        or linter.get("exclude_type_checking_imports") is not False
        or linter.get("include_external_packages") is not True
    ):
        raise ValueError("Import Linter scan scope changed")
    contracts = sequence(linter["contracts"])
    if len(contracts) < 2:
        raise ValueError("Expected import contracts are absent")
    for item in contracts:
        contract = mapping(item)
        if set(contract) - {"name", "type", "source_modules", "forbidden_modules"}:
            raise ValueError("Import contract has an unreviewed exception or option")
        for field in ("source_modules", "forbidden_modules"):
            if not sequence(contract[field]):
                raise ValueError("Empty import contract")
    required = {
        "Core cannot depend on orchestration or providers": (
            {"pythonstan.analysis.domains", "pythonstan.analysis.contracts"},
            {
                "pythonstan.api",
                "pythonstan.session",
                "pythonstan.cli",
                "pythonstan.llm",
                "pythonstan_inspector",
            },
        ),
        "Checker cannot depend on candidate generation": (
            {"pythonstan.analysis.verification"},
            {"pythonstan.llm", "openai"},
        ),
    }
    by_name = {string(mapping(item)["name"]): mapping(item) for item in contracts}
    for name, (sources, forbidden) in required.items():
        if name not in by_name:
            raise ValueError(f"Required import contract missing: {name}")
        contract = by_name[name]
        actual_sources = {string(value) for value in sequence(contract["source_modules"])}
        actual_forbidden = {string(value) for value in sequence(contract["forbidden_modules"])}
        if (
            contract.get("type") != "forbidden"
            or not actual_sources >= sources
            or not actual_forbidden >= forbidden
        ):
            raise ValueError(f"Import contract weakened: {name}")
    return len(contracts)


def import_contracts_passed(stdout: str, expected: int) -> bool:
    matches = re.findall(r"Contracts: (\d+) kept, (\d+) broken\.", stdout)
    return matches == [(str(expected), "0")]


def reject_source_suppressions(root: Path, frontier: set[str], paths: tuple[str, ...]) -> list[str]:
    failures: list[str] = []
    for path in paths:
        if (root / path).with_suffix(".pyi").exists():
            failures.append(f"Implementation shadowed by a stub: {path}")
        with (root / path).open("rb") as source:
            for token in tokenize.tokenize(source.readline):
                if token.type != tokenize.COMMENT:
                    continue
                text = token.string.lower()
                if re.match(r"#\s*mypy\s*:", text):
                    failures.append(
                        f"Per-source mypy configuration is forbidden: {path}:{token.start[0]}"
                    )
                if re.search(r"#\s*(?:type\s*:\s*ignore|noqa)", text):
                    scope = "Migrated frontier" if path in frontier else "Production source"
                    failures.append(
                        f"{scope} cannot use diagnostic suppression: {path}:{token.start[0]}"
                    )
    return failures


def compare_reviewed_policy(
    root: Path, reference: str, baseline: Mapping[str, JsonValue], frontier: Mapping[str, JsonValue]
) -> list[str]:
    failures: list[str] = []
    valid = subprocess.run(
        ["git", "rev-parse", "--verify", f"{reference}^{{commit}}"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if valid.returncode:
        raise ValueError("Comparison reference is not an available git commit")
    for filename, current in (("quality_debt.json", baseline), ("quality_frontier.json", frontier)):
        path = f"docs/development/next/{filename}"
        result = subprocess.run(
            ["git", "show", f"{reference}:{path}"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            # Initial addition still binds all debt to the frozen source tree.
            continue
        previous = mapping(json_value(json.loads(result.stdout)))
        if filename == "quality_frontier.json":
            old_modules = {string(item) for item in sequence(previous["modules"])}
            new_modules = {string(item) for item in sequence(current["modules"])}
            if not old_modules <= new_modules:
                failures.append("Typed frontier cannot shrink")
        else:
            if current != previous:
                failures.append("Frozen debt metadata and counted entries cannot change")
    return failures
