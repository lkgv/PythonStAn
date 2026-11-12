"""Source controls for namespace setdefault and its real publication consumers."""

from __future__ import annotations

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import require_object


@pytest.mark.parametrize("engine", ["ai_primary", "hybrid"])
@pytest.mark.parametrize(
    "source",
    [
        "view=globals()\n"
        "first=view.setdefault('fresh','after')\n"
        "second=view.setdefault('fresh','wrong')\n"
        "result=fresh+first+second\n",
        "class C:pass\n"
        "owner=C()\n"
        "view=vars(owner)\n"
        "first=view.setdefault('value','after')\n"
        "second=view.setdefault('value','wrong')\n"
        "result=owner.value+first+second\n",
    ],
)
def test_setdefault_preserves_existing_value_and_publishes_new_binding(
    source: str, engine: str
) -> None:
    result = analyze(
        Source(source, module="subject"),
        config={
            "engine": engine,
            "resources": {"max_work": 50000, "max_seconds": 0, "max_memory_mb": 0},
        },
        queries=["values"],
    )
    value = require_object(result.query(ValueQuery("subject", "result")).value)
    assert value["constants"] == ["afterafterafter"]
    assert value["top"] is False
    assert value["top_sorts"] == []
    assert value["bottom"] is False
    assert value["may_unbound"] is False
    assert result.coverage_status == "closed_under_profile"
