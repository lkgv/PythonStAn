"""Actual attribute services against independent scalar and hierarchy controls."""

from __future__ import annotations

import ast
from itertools import product

import pytest

from pythonstan.analysis.contracts.identity import ContextId, FrameId, ObjectId
from pythonstan.analysis.contracts.json_data import validate_json
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.attributes import AttributeOperations
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.state.flow import NormalExprFlow, RaiseFlow
from pythonstan.analysis.state.store import Frame, HeapObject, Store
from pythonstan.frontend.source import source_position
from pythonstan.models.exceptions import (
    builtin_exception_ancestors,
    canonical_builtin_exception_name,
    require_builtin_exception_name,
)


def components() -> tuple[AttributeOperations, Store, Frame]:
    driver = InterproceduralDriver({})
    frame = Frame("subject", "subject", ContextId(()), FrameId("module:subject"), is_module=True)
    return driver.transfer.attributes, Store(), frame


@pytest.mark.parametrize(
    ("constant", "attribute"),
    product((-7, 0, False, True, 1.25, -0.0), ("real", "imag", "numerator", "denominator")),
)
def test_numeric_attribute_service_matches_trusted_builtin_scalar_properties(
    constant: int | float, attribute: str
) -> None:
    service, store, frame = components()
    flows = service.getattr(Value.constant(constant), attribute, store, frame, None)
    try:
        expected = validate_json(getattr(constant, attribute))
    except AttributeError:
        assert len(flows) == 1 and isinstance(flows[0], RaiseFlow)
        assert flows[0].exception == "AttributeError"
    else:
        assert len(flows) == 1 and isinstance(flows[0], NormalExprFlow)
        assert flows[0].value == Value.constant(expected)
        assert not flows[0].store.obligations


def test_lookup_presence_separates_value_from_may_missing() -> None:
    service, store, _ = components()
    base, child = ObjectId("class:base"), ObjectId("class:child")
    store.heap[base] = HeapObject("class", {"x": Value.constant("base")})
    store.heap[child] = HeapObject(
        "class", {"x": Value.constant("child")}, maybe_missing={"x"}, metadata={"mro": (base,)}
    )
    value, missing = service.lookup_presence(child, "x", store)
    assert value.exact_constants() == ["base", "child"] and not missing
    store.heap[base].maybe_missing.add("x")
    assert service.lookup_presence(child, "x", store)[1]
    assert service.lookup_presence(ObjectId("builtin:object"), "absent", store) == (
        Value.bottom(),
        True,
    )


def test_actual_c3_diamond_order_and_inconsistent_or_havoc_hierarchy() -> None:
    service, store, _ = components()
    a, b, c, x, y = (ObjectId(name) for name in ("A", "B", "C", "X", "Y"))
    store.heap[a] = HeapObject("class")
    store.heap[b] = HeapObject("class", metadata={"mro": (a,)})
    store.heap[c] = HeapObject("class", metadata={"mro": (a,)})
    assert service.c3((b, c), store) == (b, c, a)
    with pytest.raises(ValueError, match="duplicate base"):
        service.c3((a, a), store)
    store.heap[x] = HeapObject("class", metadata={"mro": (b, c, a)})
    store.heap[y] = HeapObject("class", metadata={"mro": (c, b, a)})
    with pytest.raises(ValueError, match="inconsistent MRO"):
        service.c3((x, y), store)
    store.heap[b].havoc = True
    with pytest.raises(LookupError, match="havoc class hierarchy"):
        service.c3((b, c), store)


def test_exception_alias_identity_and_unknown_evidence_are_separate() -> None:
    assert require_builtin_exception_name("IOError") == "OSError"
    assert canonical_builtin_exception_name("EnvironmentError") == "OSError"
    assert {"OSError", "Exception", "BaseException", "IOError", "EnvironmentError"} <= (
        builtin_exception_ancestors("FileNotFoundError")
    )
    assert builtin_exception_ancestors(None) == builtin_exception_ancestors("unknown") == set()
    assert canonical_builtin_exception_name(None) is None
    with pytest.raises(ValueError, match="Unknown builtin exception"):
        require_builtin_exception_name("unknown")


def test_source_position_keeps_recorded_coordinates_and_generated_origin() -> None:
    expression = ast.parse("(\n  value\n)", mode="eval").body
    assert source_position(expression) == (2, 2)
    assert source_position(ast.Constant(value=1)) == (0, 0)
    malformed = ast.Constant(value=1)
    vars(malformed)["lineno"] = "invalid"
    with pytest.raises(TypeError, match="AST source positions"):
        source_position(malformed)
