"""CPython witnesses for versioned Native writes and detached error prefixes."""

from __future__ import annotations

import ast

import pytest

from pythonstan.analysis.composition.region_engine import NativeRegionEngine
from pythonstan.analysis.contracts.identity import FrameId, SourceModule
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.engines.abstract.transfer import Transfer
from pythonstan.analysis.state.flow import Flow, NormalStmtFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import Frame, Store
from pythonstan.config import ConfigurationError

from .domain_oracles import concrete_result, public_answer, require_members


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize(
    "setup,statement,handler,read,expected",
    [
        ("a=[0];b=[];x=0", "(a[0],b[0],x)=(1,2,3)", "IndexError", "a[0]*10+x", 10),
        ("a=[0];b=[];x=0", "(x,b[0],a[0])=(1,2,3)", "IndexError", "x*10+a[0]", 10),
        ("a=[0];b=[0];x=0", "(a[0],(b[0],x))=(1,(2,))", "ValueError", "a[0]*10+b[0]+x", 10),
        ("a=[0];b=[0];x=0", "(a[0],b[0])=(1,2,3)", "ValueError", "a[0]*10+b[0]", 0),
        ("a=[0];x=0", "a[0]=x=7", "Exception", "a[0]*10+x", 77),
        ("a=[0];b=[];x=0", "a[0]=b[0]=x=7", "IndexError", "a[0]*10+x", 70),
        ("a=[0,0]", "(a[0],a[a[0]])=(1,2)", "Exception", "a[0]*10+a[1]", 12),
        ("a=[0];b=[0]", "(a,a[0])=(b,7)", "Exception", "b[0]", 7),
        ("a=[0];x=0", "(a[0],x)=(1,1/0)", "ZeroDivisionError", "a[0]*10+x", 0),
        ("a=[0];x=0", "(a[0],x)=(1,missing)", "NameError", "a[0]*10+x", 0),
        ("a=[0];x=0", "(a[0],x)=3", "TypeError", "a[0]*10+x", 0),
    ],
)
def test_ordered_native_prefix_matches_independent_cpython(
    setup: str, statement: str, handler: str, read: str, expected: int, native: bool
) -> None:
    source = (
        setup
        + "\ntry:\n    "
        + statement
        + "\nexcept "
        + handler
        + ":\n    pass\nresult="
        + read
        + "\n"
    )
    assert concrete_result(source) == expected
    require_members(public_answer(source, native_line=3 if native else None), [expected])


@pytest.mark.parametrize("native", [False, True])
def test_finally_observes_only_completed_target_prefix(native: bool) -> None:
    source = (
        "a=[0];b=[];x=0\ntry:\n    (a[0],b[0],x)=(1,2,3)\n"
        "except IndexError:\n    pass\nfinally:\n    result=a[0]*10+x\n"
    )
    assert concrete_result(source) == 10
    require_members(public_answer(source, native_line=3 if native else None), [10])


@pytest.mark.parametrize("scope", ["module", "class"])
def test_runtime_annotation_namespace_is_rejected_before_any_native_write(
    monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    source = "x=0\ntry:\n    x: missing = 1\nexcept NameError:\n    result=x\n"
    assert concrete_result(source) == 1
    driver = InterproceduralDriver((SourceModule("subject", "subject.py", "x: missing = 1\n"),))
    frame = Frame(
        "subject",
        "subject" if scope == "module" else "subject.C",
        (),
        FrameId("root"),
        is_module=scope == "module",
        is_class=scope == "class",
    )
    store = Store(cells={frame.cell("x"): Value.constant(0)})
    flow = NormalStmtFlow(StateHandle.capture(store))
    calls = 0

    def forbidden_ai(
        self: Transfer, node: ast.stmt, state: Store, current: Frame, **kwargs: object
    ) -> list[Flow]:
        nonlocal calls
        calls += 1
        raise AssertionError("Native capability rejection must not replay through AI")

    monkeypatch.setattr(Transfer, "statement", forbidden_ai)
    native = NativeRegionEngine()
    before = driver.work
    with pytest.raises(ConfigurationError, match="runtime annotation namespaces"):
        native.execute(driver.trees["subject"].body[0], flow, frame, driver)
    assert calls == 0 and native.runs == 0 and not native.trace
    assert driver.work == before and not driver.writes and not store.writes
    assert store.cells[frame.cell("x")].exact_constants() == [0]


def test_function_local_annotation_remains_a_supported_unexecuted_annotation() -> None:
    source = "def f():\n    value: missing = 7\n    return value\nresult=f()\n"
    assert concrete_result(source) == 7
    require_members(public_answer(source, native_line=2), [7])
