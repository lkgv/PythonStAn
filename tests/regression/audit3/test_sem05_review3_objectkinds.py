"""Real Python duck classes can be module and descriptor-wrapper objects."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pythonstan import Project, Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import require_object

from .test_sem05_review3 import _member

_GOLD = Path(__file__).with_name("data") / "sem05_review3_objectkind_cases.json"
CASES: tuple[dict[str, object], ...] = tuple(json.loads(_GOLD.read_text())["cases"])


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["id"]))
def test_other_own_bases_carriers(case: dict[str, object], tmp_path: Path) -> None:
    if "files" in case:
        files = case["files"]
        assert isinstance(files, dict)
        for name, text in files.items():
            (tmp_path / str(name)).write_text(str(text))
        target = Project(tmp_path, entries=("main:",))
        query = ValueQuery("main", "result")
    else:
        target = Source(str(case["source"]), module="subject")
        query = ValueQuery("subject", "result")
    result = analyze(
        target,
        config={
            "engine": "ai_primary",
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=[query],
    )
    answer = result.query(query)
    row = {
        "id": case["id"],
        "expected": case["expected"],
        "answer_status": answer.status,
        "value": answer.value,
        "solver": result.solver_status,
        "coverage": result.coverage_status,
        "obligations": result.raw_snapshot().get("obligations", []),
    }
    output = Path("artifacts/next/evidence/SEM05_REVIEW3/objectkinds-before")
    output.mkdir(parents=True, exist_ok=True)
    (output / (str(case["id"]) + ".json")).write_text(json.dumps(row, indent=2) + "\n")
    assert answer.status == "answered", row
    assert _member(require_object(answer.value), case["expected"]), row
