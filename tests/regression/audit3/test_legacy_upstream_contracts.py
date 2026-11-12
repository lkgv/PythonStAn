"""Actual foreign predecessor wrappers retain their consumed state and effects."""

from __future__ import annotations

import ast
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from pythonstan.analysis.analysis import AnalysisConfig
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.compat.legacy_ai.analysis import AIAnalysisDriver
from pythonstan.compat.legacy_ai.upstream import LegacyProvider
from pythonstan.ir import IRModule
from pythonstan.world.world import World


class _ForeignQuery:
    _state: PointerAnalysisState

    def __init__(self, state: PointerAnalysisState) -> None:
        self._state = state


class _ForeignResult:
    value: _ForeignQuery
    observations: list[str]

    def __init__(self, query: _ForeignQuery, observations: list[str]) -> None:
        self.value = query
        self.observations = observations

    def query(self) -> _ForeignQuery:
        self.observations.append("query")
        return self.value

    def get_statistics(self) -> dict[str, object]:
        self.observations.append("statistics")
        return {"converged": 1, "unconsumed": object()}


def test_real_driver_accepts_foreign_query_and_result_wrappers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = PointerAnalysisState()
    observations: list[str] = []
    foreign = _ForeignResult(_ForeignQuery(state), observations)
    monkeypatch.setattr(World(), "analysis_manager", None, raising=False)
    driver = AIAnalysisDriver(
        AnalysisConfig("ai", "AIAnalysisDriver", options={"type": "ai analysis"})
    )
    result = driver.analyze(IRModule("subject", ast.parse("pass")), {"kcfa": foreign})
    assert result.summaries == {}
    assert result is driver.results
    assert observations == ["statistics", "query"]
    checked = LegacyProvider(foreign).query()
    assert checked._state is state


def test_services_keep_legacy_double_lookup_without_calling_their_truth_protocol() -> None:
    state = PointerAnalysisState()
    observations: list[str] = []

    class Service:
        def __call__(self) -> object:
            observations.append("call")
            return _ForeignQuery(state)

        def __bool__(self) -> bool:
            raise AssertionError("The original query expression never tested service truth")

    class Result:
        @property
        def query(self) -> Callable[[], object]:
            observations.append("lookup")
            return Service()

    assert LegacyProvider(Result()).query()._state is state
    assert observations == ["lookup", "lookup", "call"]


def test_invalid_native_state_is_rejected_before_any_complete_result_is_published() -> None:
    with pytest.raises(TypeError, match="Native state contract"):
        LegacyProvider(SimpleNamespace(_state=object())).query()
    with pytest.raises(RuntimeError, match="does not expose analysis state"):
        LegacyProvider(SimpleNamespace(_state=None)).query()


def test_convergence_observation_keeps_the_raw_value_and_unconsumed_payload() -> None:
    marker = object()
    untouched = object()
    metadata = {"converged": marker, "other": untouched}

    class Result:
        def get_statistics(self) -> dict[str, object]:
            return metadata

    observed = LegacyProvider(Result()).get_statistics()
    assert observed["converged"] is marker
    assert metadata["other"] is untouched
    assert observed is not metadata
