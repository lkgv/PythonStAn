"""Missing optional fact capability cannot become a complete empty catalog."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import replace

import pytest

from pythonstan import Source, analyze
from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider
from pythonstan.analysis.composition.summary_context import SummaryApplicationContext
from pythonstan.analysis.contracts.identity import ObjectId
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.observations.driver_bridge import SummaryProcedureExit
from pythonstan.analysis.state.flow import Flow
from pythonstan.analysis.state.store import Frame, Store


@pytest.mark.parametrize("drop_all", (True, False))
def test_genuine_accepted_application_with_missing_fact_capability_stays_open(
    monkeypatch: pytest.MonkeyPatch, drop_all: bool
) -> None:
    original = GuardedDispatchProvider.try_apply

    def without_facts(
        self: GuardedDispatchProvider,
        context: SummaryApplicationContext,
        identifier: ObjectId,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        site: ast.AST | None,
    ) -> list[Flow] | None:
        callback = context.record_procedure_exits
        port = None
        if not drop_all and callback is not None:

            def only_dispatcher(facts: tuple[SummaryProcedureExit, ...]) -> None:
                assert callback is not None
                callback(tuple(fact for fact in facts if fact.qualname.endswith(".dispatch")))

            port = only_dispatcher
        return original(
            self,
            replace(context, record_procedure_exits=port),
            identifier,
            args,
            keywords,
            store,
            frame,
            site,
        )

    monkeypatch.setattr(GuardedDispatchProvider, "try_apply", without_facts)
    source = (
        "def identity(payload):\n    return payload\n"
        "def dispatch(table,key,payload):\n    return table[key](payload)\n"
        "table={'read':identity}\nplain=dispatch(table,'read','original')\n"
    )
    result = analyze(
        Source(source, module="m"),
        config={
            "engine": "ai_primary",
            "llm": {"enabled": True, "level2": True, "provider": "offline"},
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=["returns", "outcomes"],
    )
    answer = result.query("returns")
    assert answer.coverage_status == "open"
    assert answer.status == ("unavailable" if drop_all else "answered")
