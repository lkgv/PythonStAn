"""Finite, independent membership oracles for mandatory audit regressions.

These helpers interpret published wire data and simple lattice descriptions.
They never use the analyzer's transfers or order relation to establish expected
answers. A passing finite check is regression evidence, not a soundness proof.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Iterable, Mapping

from pythonstan import Source, ValueQuery, analyze

type Scalar = None | bool | int | float | str
type JsonValue = Scalar | list[JsonValue] | dict[str, JsonValue]


def concrete_result(source: str, bindings: Mapping[str, Scalar] | None = None) -> Scalar:
    """Evaluate an approved tiny fixture in an isolated CPython 3.12 process."""
    assert sys.version_info[:2] == (3, 12), "The frozen oracle profile is CPython 3.12"
    prelude = "\n".join(f"{name} = {value!r}" for name, value in (bindings or {}).items())
    completed = subprocess.run(
        [sys.executable, "-I", "-c", prelude + "\n" + source + "\nprint(repr(result))"],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )
    value: object = ast.literal_eval(completed.stdout.strip())
    assert value is None or isinstance(value, (bool, int, float, str))
    return value


def public_answer(
    source: str,
    *,
    bindings: Mapping[str, JsonValue] | None = None,
    abstract: Mapping[str, JsonValue] | None = None,
    native_line: int | None = None,
    regions: list[JsonValue] | None = None,
) -> dict[str, JsonValue]:
    """Run the real public API; errors and unavailable answers fail the caller."""
    config: dict[str, JsonValue] = {
        "engine": "ai_primary",
        "resources": {"max_work": 10000, "max_seconds": 8, "max_memory_mb": 1536},
    }
    if bindings:
        config["inputs"] = {
            "bindings": {f"subject.{key}": value for key, value in bindings.items()}
        }
    if abstract:
        config["abstract"] = dict(abstract)
    if native_line is not None:
        assert regions is None
        regions = [
            {
                "selector": {"module": "subject", "kind": "block", "line_start": native_line},
                "engine": "native",
            }
        ]
    if regions:
        config["regions"] = regions
    result = analyze(
        Source(source, module="subject"),
        config=config,
        queries=(ValueQuery("subject", "result"), "effects", "outcomes", "stats"),
    ).to_dict()
    # The JSON round trip explicitly separates the wire oracle from internal Value.
    payload: object = json.loads(json.dumps(result))
    assert isinstance(payload, dict)
    answers = payload["answers"]
    assert isinstance(answers, dict)
    for answer in answers.values():
        assert isinstance(answer, dict)
        query = answer["query"]
        assert isinstance(query, dict)
        if query.get("kind") == "value":
            assert answer["status"] == "answered"
            assert answer["coverage_status"] == "closed_under_profile"
            value = answer["value"]
            assert isinstance(value, dict)
            assert not value["top"] and not value["top_sorts"], "Mandatory precision cannot be Top"
            return value
    raise AssertionError("The mandatory result query was not materialized")


def wire_contains(value: Mapping[str, object], member: Scalar) -> bool:
    """Independent finite wire membership, preserving residual pattern unions."""
    if value.get("top"):
        return True
    tagged = value.get("tagged_constants", [])
    assert isinstance(tagged, list)
    for item in tagged:
        assert isinstance(item, dict)
        if type(item.get("value")) is type(member) and item.get("value") == member:
            return True
    if type(member) is int:
        interval = value.get("integer_interval")
        if isinstance(interval, dict):
            lower, upper = interval["lower"], interval["upper"]
            parity = interval["parity"]
            return (
                (lower is None or isinstance(lower, int) and lower <= member)
                and (upper is None or isinstance(upper, int) and member <= upper)
                and (
                    parity == "top"
                    or parity == "even"
                    and member % 2 == 0
                    or parity == "odd"
                    and member % 2 != 0
                )
            )
    if isinstance(member, str):
        patterns = value.get("string_patterns", {})
        assert isinstance(patterns, dict)
        if patterns.get("unknown"):
            return True
        if any(member.startswith(prefix) for prefix in patterns.get("prefixes", [])):
            return True
        if any(member.endswith(suffix) for suffix in patterns.get("suffixes", [])):
            return True
        return any(
            member.startswith(pair[0]) and member.endswith(pair[1])
            for pair in patterns.get("conjunctions", [])
        )
    return False


def require_members(value: Mapping[str, object], expected: Iterable[Scalar]) -> None:
    for member in expected:
        assert wire_contains(value, member), f"Wire result omits {member!r}: {value!r}"
