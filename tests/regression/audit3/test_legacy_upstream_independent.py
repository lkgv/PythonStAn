"""Independent predecessor metadata probes through the actual default factory."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from typing import override

import pytest

from pythonstan.analysis.analysis import AnalysisConfig
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.object import AllocKind, AllocSite, ModuleObject
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.compat.legacy_ai.analysis import AIAnalysisDriver
from pythonstan.compat.legacy_ai.upstream import LegacyProvider
from pythonstan.ir.ir_statements import IRFunc, IRModule


class _State(PointerAnalysisState):
    def __bool__(self) -> bool:
        raise AssertionError("State identity must not be tested by truthiness")


class _Query:
    def __init__(self, state: PointerAnalysisState, events: list[str]) -> None:
        self.state: PointerAnalysisState = state
        self.events: list[str] = events
        self.error: RuntimeError | None = None

    @property
    def _state(self) -> PointerAnalysisState:
        self.events.append("query.state")
        if self.error is not None:
            raise self.error
        return self.state


class _Statistics:
    def __init__(self, events: list[str]) -> None:
        self.events: list[str] = events
        self.error: RuntimeError | None = None

    @property
    def get(self) -> Callable[[str], object]:
        self.events.append("statistics.get")

        def lookup(name: str) -> object:
            self.events.append("statistics.lookup:" + name)
            if self.error is not None:
                raise self.error
            if name != "converged":
                raise AssertionError("The driver may only consume convergence metadata")
            return 1

        return lookup


class _Result:
    def __init__(self, state: PointerAnalysisState, events: list[str]) -> None:
        self.events: list[str] = events
        self.statistics: _Statistics = _Statistics(events)
        self.solver_query: _Query = _Query(state, events)
        self.statistics_reads: int = 0
        self.query_reads: int = 0
        self.second_statistics_error: RuntimeError | None = None
        self.query_call_error: RuntimeError | None = None

    def __bool__(self) -> bool:
        raise AssertionError("Predecessor presence uses identity, not truthiness")

    @property
    def get_statistics(self) -> Callable[[], object]:
        self.statistics_reads += 1
        self.events.append("result.statistics:" + str(self.statistics_reads))
        if self.statistics_reads == 2 and self.second_statistics_error is not None:
            raise self.second_statistics_error

        def read() -> object:
            self.events.append("result.statistics.call")
            return self.statistics

        return read

    @property
    def query(self) -> Callable[[], object]:
        self.query_reads += 1
        self.events.append("result.query:" + str(self.query_reads))

        def read() -> object:
            self.events.append("result.query.call")
            if self.query_call_error is not None:
                raise self.query_call_error
            return self.solver_query

        return read


def _driver() -> AIAnalysisDriver:
    return AIAnalysisDriver(
        AnalysisConfig("ai", "AIAnalysisDriver", options={"type": "ai analysis"})
    )


def test_default_factory_preserves_raw_and_native_state_identity_and_access_order() -> None:
    events: list[str] = []
    state = _State()
    raw = _Result(state, events)
    driver = _driver()
    provider = driver._get_pta_results({"kcfa": raw})
    assert provider.raw is raw
    assert provider.get_statistics() == {"converged": 1}
    assert driver._get_solver_query(provider)._state is state
    assert events == [
        "result.statistics:1",
        "result.statistics:2",
        "result.statistics.call",
        "statistics.get",
        "statistics.lookup:converged",
        "result.query:1",
        "result.query:2",
        "result.query.call",
        "query.state",
    ]


def test_query_like_raw_predecessor_keeps_one_external_state_read() -> None:
    events: list[str] = []
    state = _State()
    raw = _Query(state, events)
    provider = _driver()._get_pta_results({"kcfa": raw})
    assert provider.get_statistics() == {}
    assert provider.query()._state is state
    assert events == ["query.state"]


@pytest.mark.parametrize("location", ["statistics_getter", "statistics_lookup", "query", "state"])
def test_external_service_failures_preserve_exception_identity(location: str) -> None:
    events: list[str] = []
    raw = _Result(_State(), events)
    marker = RuntimeError("external " + location)
    if location == "statistics_getter":
        raw.second_statistics_error = marker
    elif location == "statistics_lookup":
        raw.statistics.error = marker
    elif location == "query":
        raw.query_call_error = marker
    else:
        raw.solver_query.error = marker
    provider = _driver()._get_pta_results({"kcfa": raw})
    with pytest.raises(RuntimeError) as failed:
        if location.startswith("statistics"):
            provider.get_statistics()
        else:
            provider.query()
    assert failed.value is marker


@dataclass(frozen=True)
class _OpaqueStatePayload:
    _state: object


def test_default_decoder_rejects_non_native_state_without_retyping_it() -> None:
    provider = LegacyProvider(_OpaqueStatePayload(object()))
    with pytest.raises(TypeError, match="Native state contract"):
        provider.query()


def test_missing_state_remains_an_explicit_predecessor_failure() -> None:
    provider = LegacyProvider(_OpaqueStatePayload(None))
    with pytest.raises(RuntimeError, match="does not expose analysis state"):
        provider.query()


def test_noncallable_optional_query_is_not_mistaken_for_absence() -> None:
    class InvalidQuery:
        query = 0

    with pytest.raises(TypeError, match="'int' object is not callable"):
        LegacyProvider(InvalidQuery()).query()


def test_nominal_native_state_fallback_reads_internal_scope_metadata_once() -> None:
    class FallbackState(PointerAnalysisState):
        def __init__(self) -> None:
            self.reads: int = 0
            super().__init__()

        @override
        def __getattribute__(self, name: str) -> object:
            if name == "get_scopes_for_ir":
                raise AttributeError(name)
            if name == "_internal_scope":
                reads: object = object.__getattribute__(self, "reads")
                assert isinstance(reads, int)
                object.__setattr__(self, "reads", reads + 1)
                if reads:
                    raise RuntimeError("The second internal-scope getter must not run")
            value: object = object.__getattribute__(self, name)
            return value

    ir = IRModule("probe", ast.parse(""), filename="probe.py")
    context = CallStringContext((), 0)
    module = ModuleObject(context, AllocSite(ir, AllocKind.MODULE), ir)
    scope = Scope.new(module, None, context, ir)
    state = FallbackState()
    object.__setattr__(state, "_internal_scope", {module: scope})
    assert _driver()._get_scope_contexts(state, ir) == [scope]
    assert state.reads == 1


def test_nominal_ast_argument_subclass_retains_legacy_truth_check() -> None:
    class FalsyArgument(ast.arg):
        def __bool__(self) -> bool:
            return False

    node = ast.parse("def target(*items, **extras): pass").body[0]
    assert isinstance(node, ast.FunctionDef)
    node.args.vararg = FalsyArgument(arg="items")
    node.args.kwarg = FalsyArgument(arg="extras")
    function = IRFunc("target", node)
    assert _driver()._get_param_names(function) == []
