"""Independent W2 witnesses, frozen before accepting implementation repairs.

Finite CPython observations establish expected scalar and prefix behavior.
Passing these examples is regression evidence, not a general soundness proof.
"""

from __future__ import annotations

import ast
import itertools
import operator
from collections.abc import Callable

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.identity import CellId, ObjectId, StateRevision
from pythonstan.analysis.domains.ints import IntLattice
from pythonstan.analysis.domains.strings import StrLattice
from pythonstan.analysis.domains.values import Value, ValueOperations, ValuePolicy
from pythonstan.analysis.state.guards import MembershipGuard
from pythonstan.analysis.state.store import HeapObject, Store
from pythonstan.analysis.state.transform import payload_signature

from .domain_oracles import Scalar, concrete_result, wire_contains


def _contains(value: Value, member: Scalar) -> bool:
    sort = {type(None): "none", bool: "bool", int: "int", float: "float", str: "str"}[type(member)]
    return value.top or sort in value.top_sorts or wire_contains(value.export(), member)


@pytest.mark.parametrize(
    "operation,primitive",
    [
        (ast.Add, operator.add),
        (ast.Sub, operator.sub),
        (ast.Mult, operator.mul),
        (ast.Div, operator.truediv),
    ],
    ids=["add", "subtract", "multiply", "divide"],
)
@pytest.mark.parametrize(
    "left,right",
    [
        ((1, "q"), (2, "r")),
        ((0.0, "q"), (2, False)),
        ((True, None), (3.5, "r")),
        ((-2, 4.0), (0, 2)),
    ],
)
def test_finite_scalar_pair_coverage_survives_cap(
    operation: type[ast.operator],
    primitive: Callable[[object, object], object],
    left: tuple[Scalar, Scalar],
    right: tuple[Scalar, Scalar],
) -> None:
    ops = ValueOperations(ValuePolicy(max_scalar_pairs=1))
    abstract_left = ops.join(Value.constant(left[0]), Value.constant(left[1]))
    abstract_right = ops.join(Value.constant(right[0]), Value.constant(right[1]))
    answer, errors = ops.scalar_binary(operation(), abstract_left, abstract_right)
    expected_values: list[Scalar] = []
    expected_errors: set[str] = set()
    for first, second in itertools.product(left, right):
        try:
            concrete: object = primitive(first, second)
        except (TypeError, ZeroDivisionError) as error:
            expected_errors.add(type(error).__name__)
        else:
            assert concrete is None or isinstance(concrete, (bool, int, float, str))
            expected_values.append(concrete)
    assert expected_errors <= errors
    assert all(_contains(answer, expected) for expected in expected_values)
    assert not answer.top, "Finite scalar overflow must retain sort-specific information"


@pytest.mark.parametrize("zero", [0.0, -0.0])
@pytest.mark.parametrize("other", ["integer_interval", "string_prefix"])
def test_float_zero_truth_with_non_enumerable_other_facet(zero: float, other: str) -> None:
    value = (
        Value(ints=IntLattice.from_interval(2, None), floats=frozenset({zero}))
        if other == "integer_interval"
        else Value(strings=StrLattice.from_prefix("prefix"), floats=frozenset({zero}))
    )
    assert ValueOperations(ValuePolicy(max_scalar_pairs=1)).truth(value) == frozenset({False, True})


@pytest.mark.parametrize("prefix,residual", [("xy", "!"), ("nonempty", "outside")])
def test_export_union_keeps_pattern_residual(prefix: str, residual: str) -> None:
    source = Value(
        strings=StrLattice.from_consts(frozenset({prefix + "1", prefix + "2", residual}))
    )
    widened = ValueOperations(ValuePolicy(max_const_strings=1)).widen(source, source)
    assert all(
        wire_contains(widened.export(), member) for member in (prefix + "1", prefix + "2", residual)
    )


@pytest.mark.parametrize("sort", ["bytes", "opaque_scalar"])
def test_opaque_sort_ordering_keeps_a_normal_protocol_obligation(sort: str) -> None:
    answer, errors = ValueOperations().scalar_binary(
        ast.Lt(), Value(top_sorts=frozenset({sort})), Value(top_sorts=frozenset({sort}))
    )
    assert _contains(answer, True) and _contains(answer, False)
    assert "unknown_operator" in errors


def test_unbound_alternative_is_an_error_not_a_normal_unknown_value() -> None:
    ops = ValueOperations()
    mixed = ops.join(Value.constant(3), Value(unbound=True))
    answer, errors = ops.scalar_binary(ast.Add(), mixed, Value.constant(2))
    assert answer.exact_constants() == [5]
    assert errors == {"unbound_operand"}
    missing, missing_errors = ops.scalar_binary(ast.Add(), Value(unbound=True), Value.constant(2))
    assert missing.is_bottom()
    assert missing_errors == {"unbound_operand"}


def test_widening_stabilizes_all_owned_value_locations_uniformly() -> None:
    """Increasing length sequences need widening outside ordinary cells too."""
    roots = []
    for number in (0, 1):
        value = Value.constant(number)
        roots.append(
            Store(
                cells={CellId("counter"): value},
                heap={
                    ObjectId("L"): HeapObject(
                        "list", {"counter": value}, default=value, metadata={"length_value": value}
                    )
                },
            )
        )
    widened = roots[0].join(roots[1], ValueOperations(ValuePolicy(max_const_ints=1)), widen=True)
    obj = widened.heap[ObjectId("L")]
    values = [
        widened.cells[CellId("counter")],
        obj.fields["counter"],
        obj.default,
        obj.metadata["length_value"],
    ]
    for value in values:
        assert isinstance(value, Value)
        assert value.ints.lo == 0 and value.ints.hi is None
        assert value.ints.may_contain(50)
    assert not obj.havoc, "Typed length joins do not require discarding object semantics"


def _observation_snapshot(root: Store) -> tuple[object, ...]:
    return (
        id(root.cells),
        id(root.heap),
        root.identity_epoch,
        root.state_revision,
        payload_signature(root),
        tuple((owner, id(obj), id(obj.metadata)) for owner, obj in root.heap.items()),
    )


@pytest.mark.parametrize("operation", ["leq", "read_slot", "join"])
def test_two_stale_epochs_observation_preserves_both_owned_graphs(operation: str) -> None:
    ops = ValueOperations()
    left = Store(
        cells={CellId("pointer"): Value.ref(ObjectId("A"))}, state_revision=StateRevision(3)
    )
    left.heap[ObjectId("A")] = HeapObject(
        "generator", {"value": Value.constant(7)}, metadata={"entry": left}
    )
    left.guards["presence"] = MembershipGuard(ObjectId("A"), "value", True)
    ops.arena.publish(ops.arena.prepare({ObjectId("A"): ObjectId("B")}))
    right = Store(
        cells={CellId("pointer"): Value.ref(ObjectId("B"))},
        identity_epoch=1,
        state_revision=StateRevision(8),
    )
    right.heap[ObjectId("B")] = HeapObject(
        "generator", {"value": Value.constant(7)}, metadata={"entry": right}
    )
    right.guards["presence"] = MembershipGuard(ObjectId("B"), "value", True)
    ops.arena.publish(ops.arena.prepare({ObjectId("B"): ObjectId("S")}))
    before_left, before_right = _observation_snapshot(left), _observation_snapshot(right)
    if operation == "leq":
        assert left.leq(right, ops)
    elif operation == "read_slot":
        assert left.read_slot(ObjectId("A"), "value", ops)[0].exact_constants() == [7]
        assert right.read_slot(ObjectId("B"), "value", ops)[0].exact_constants() == [7]
    else:
        merged = left.join(right, ops)
        assert merged.heap[ObjectId("S")].metadata["entry"] is merged
    assert _observation_snapshot(left) == before_left
    assert _observation_snapshot(right) == before_right
    assert left.heap[ObjectId("A")].metadata["entry"] is left
    assert right.heap[ObjectId("B")].metadata["entry"] is right


_CALL_PRELUDE = """log=''
def mark(label, value):
    global log
    log=log+label
    return value
def f(*args, **kwargs):
    return 0
class Items:
    def __iter__(self):
        global log
        log=log+'i'
        return iter(())
"""

_BOOL_PRELUDE = """log=''
class Flag:
    def __init__(self, label, value):
        self.label=label
        self.value=value
    def __bool__(self):
        global log
        log=log+self.label
        return self.value
a=Flag('a', False)
b=Flag('b', True)
c=Flag('c', True)
"""

_SEMANTIC_CASES = [
    (
        "star_before_textual_keyword",
        _CALL_PRELUDE + "f(k=mark('k', 1), *mark('s', [2]))\nresult=log\n",
    ),
    (
        "sole_star_defers_iteration",
        _CALL_PRELUDE + "f(*mark('s', Items()), k=mark('k', 1))\nresult=log\n",
    ),
    (
        "mixed_star_iterates_before_keyword",
        _CALL_PRELUDE + "f(0, *mark('s', Items()), k=mark('k', 1))\nresult=log\n",
    ),
    (
        "duplicate_merge_stops_later_keyword",
        _CALL_PRELUDE
        + "try:\n    f(**mark('a', {'x':1}), **mark('b', {'x':2}), y=mark('c', 3))\nexcept TypeError:\n    result=log\n",
    ),
    (
        "named_group_evaluates_before_merge",
        _CALL_PRELUDE
        + "try:\n    f(**{'x':0}, x=mark('a', 1), y=mark('b', 2), **mark('c', {}))\nexcept TypeError:\n    result=log\n",
    ),
    (
        "nonstr_key_checks_after_values",
        _CALL_PRELUDE
        + "try:\n    f(**mark('a', {1:1}), y=mark('b', 2))\nexcept TypeError:\n    result=log\n",
    ),
    ("nested_bool_assignment", _BOOL_PRELUDE + "value=(a and b) or c\nresult=log\n"),
    ("three_operand_or", _BOOL_PRELUDE + "value=a or b or c\nresult=log\n"),
    ("nested_bool_condition", _BOOL_PRELUDE + "if (a and b) or c:\n    pass\nresult=log\n"),
    ("three_operand_and_condition", _BOOL_PRELUDE + "if a and b and c:\n    pass\nresult=log\n"),
    ("nested_bool_not", _BOOL_PRELUDE + "value=not (a and b)\nresult=log\n"),
    (
        "delete_builtin_has_no_read_fallback",
        "try:\n    del len\nexcept NameError:\n    result='missing'\nelse:\n    result='deleted'\n",
    ),
    (
        "delete_shadowed_builtin_is_local_error",
        "def f():\n    try:\n        del len\n    except UnboundLocalError:\n        return 'local'\n    return 'deleted'\nresult=f()\n",
    ),
    (
        "delete_module_then_read",
        "x=1\ndel x\ntry:\n    result=x\nexcept NameError:\n    result='missing'\n",
    ),
    (
        "delete_nonlocal_then_read",
        "def outer():\n    x=1\n    def inner():\n        nonlocal x\n        del x\n    inner()\n    try:\n        return x\n    except NameError:\n        return 'missing'\nresult=outer()\n",
    ),
    (
        "delete_tuple_keeps_completed_prefix",
        "x=1\ntry:\n    del x, abs\nexcept NameError:\n    try:\n        result=x\n    except NameError:\n        result='prefix'\n",
    ),
]


@pytest.mark.parametrize("engine", ["ai_primary", "hybrid"])
@pytest.mark.parametrize("name,source", _SEMANTIC_CASES, ids=[case[0] for case in _SEMANTIC_CASES])
def test_public_semantic_prefix_matches_cpython(name: str, source: str, engine: str) -> None:
    expected = concrete_result(source)
    result = analyze(
        Source(source, module="subject"),
        config={
            "engine": engine,
            "resources": {"max_work": 10000, "max_seconds": 8, "max_memory_mb": 1536},
        },
        queries=(ValueQuery("subject", "result"),),
    ).to_dict()
    answers = result["answers"]
    assert isinstance(answers, dict)
    value_answers = []
    for row in answers.values():
        assert isinstance(row, dict)
        query = row["query"]
        assert isinstance(query, dict)
        if query.get("kind") == "value":
            value_answers.append(row)
    assert len(value_answers) == 1
    answer = value_answers[0]
    assert isinstance(answer, dict)
    assert answer["status"] == "answered", (name, answer)
    assert answer["coverage_status"] == "closed_under_profile", (name, answer)
    value = answer["value"]
    assert isinstance(value, dict)
    assert not value.get("top") and not value.get("top_sorts"), (name, value)
    assert wire_contains(value, expected), (name, expected, value)
