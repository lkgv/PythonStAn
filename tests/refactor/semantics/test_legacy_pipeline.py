"""Legacy compatibility regressions read AI full values, never Native results."""

import json
from pathlib import Path
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[3]

CASES = [
    ("scalar_string", "def outer(x):\n    return x\nresult = outer('after')\n", "string_constants", ["after"]),
    ("lifted_literal", "def inner(x):\n    return x\ndef outer():\n    return inner('after')\nresult = outer()\n", "string_constants", ["after"]),
    ("local_control", "def inner(x):\n    return x\ndef outer():\n    value = 'after'\n    return inner(value)\nresult = outer()\n", "string_constants", ["after"]),
    ("kwargs", "def inner(*, x):\n    return x\ndef outer():\n    values = {'x': 'after'}\n    return inner(**values)\nresult = outer()\n", "string_constants", ["after"]),
    ("definition_default", "def inner(x='after'):\n    return x\ndef outer():\n    return inner()\nresult = outer()\n", "string_constants", ["after"]),
    ("finally_override", "def outer():\n    try:\n        return 1\n    finally:\n        return 2\nresult = outer()\n", "integer_constants", [2]),
    ("nonlocal", "def outer():\n    x=1\n    def write():\n        nonlocal x\n        x=2\n    write()\n    return x\nresult = outer()\n", "integer_constants", [2]),
]


@pytest.mark.parametrize("name,source,component,expected", CASES, ids=[row[0] for row in CASES])
def test_legacy_ai_full_summary(name, source, component, expected):
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.refactor.legacy_probe"],
        input=json.dumps({"source": source}), cwd=REPO,
        capture_output=True, text=True, timeout=12,
    )
    assert completed.returncode == 0, completed.stderr
    observation = json.loads(completed.stdout)
    assert observation["query_backend"] == "legacy_AI_full_summary_value"
    assert observation["full_value_present"] is True
    assert observation["complete"] is True, observation
    assert observation[component] == expected, observation
    assert observation["full_value_bottom"] is False
