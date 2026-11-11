"""Finite containment checks for literal sequence sizes and unbounded joins."""
import ast
import pytest
from pythonstan.analysis.pointer.ai import AbsVal, AIState, AnalysisBudget, IRInterpreter
from pythonstan.analysis.pointer.ai.domains.ints import IntDomain, IntLattice
from pythonstan.analysis.pointer.ai.heap import ContainerSlots
from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
from pythonstan.ir.ir_statements import IRCall


@pytest.mark.parametrize("left,right,witnesses", [
    (IntLattice.nonneg(), IntLattice.nonneg(), (0, 1, 10**12)),
    (IntLattice.from_interval(None, -1), IntLattice.const(2), (-10**12, -1, 2)),
    (IntLattice.from_interval(4, None), IntLattice.const(0), (0, 4, 10**12)),
])
def test_unbounded_integer_join_retains_all_input_witnesses(left, right, witnesses):
    domain = IntDomain()
    result = domain.join(left, right)
    assert domain.leq(left, result) and domain.leq(right, result)
    assert all(result.may_contain(value) for value in witnesses)
    assert domain.leq(left, domain.meet(left, left))


def test_container_length_participates_in_order_and_widening():
    empty = ContainerSlots.empty_sequence()
    one = empty.append(AbsVal.from_int(42))
    assert empty.length.get_consts() == frozenset({0})
    assert one.length.lo == one.length.hi == 1
    joined = empty.join(one)
    assert empty.leq(joined) and one.leq(joined)
    widened = empty.widen(one)
    assert widened.length.may_contain(0) and widened.length.may_contain(100000)
    assert one.get_elem().get_int_lattice().get_consts() == frozenset({42})


def test_len_name_without_builtin_identity_uses_unknown_call_effects():
    # The symbol len may refer to arbitrary user code. Its spelling alone is
    # insufficient authorization to apply the pure built-in length transfer.
    summary = SummaryAccumulator()
    stmt = IRCall(ast.parse("n = len(items)").body[0])
    result = IRInterpreter(None, AnalysisBudget()).transfer(stmt, AIState(), summary)
    assert result.states[0][1].get_var("n").addr_top
    assert "unknown_call" in summary.to_summary().incomplete_reasons


def test_unknown_call_forgets_mutable_size_but_retains_element_witness():
    from pythonstan.analysis.pointer.ai.heap import AIAddr, AbsObj, ObjKind
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext
    obj = AbstractObject(CallStringContext(), AllocSite("list", AllocKind.LIST))
    addr = AIAddr.from_kcfa_obj(obj)
    slots = ContainerSlots.empty_sequence().append(AbsVal.from_int(42))
    state = AIState().set_obj(addr, AbsObj(addr, ObjKind.LIST, container_slots=slots))
    state = state.set_var("items", AbsVal.from_addrs(frozenset({obj})))
    stmt = IRCall(ast.parse("unknown_mutator()").body[0].value)
    result = IRInterpreter(None, AnalysisBudget()).transfer(stmt, state, SummaryAccumulator())
    changed = result.states[0][1].get_obj(addr).container_slots
    assert changed.length.may_contain(0) and changed.length.may_contain(10000)
    assert changed.get_elem().get_int_lattice().get_consts() == frozenset({42})
    assert state.get_obj(addr).container_slots.length.lo == 1


@pytest.mark.parametrize("operation", ["store_alias", "attribute_hook", "opaque"])
def test_alias_or_implicit_hook_operations_forget_old_size(operation):
    from pythonstan.analysis.pointer.ai.heap import AIAddr, AbsObj, ObjKind
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext
    from pythonstan.ir.ir_statements import IRStoreSubscr, IRLoadAttr, IRAstStmt
    obj = AbstractObject(CallStringContext(), AllocSite("list", AllocKind.LIST))
    addr = AIAddr.from_kcfa_obj(obj)
    state = AIState().set_obj(addr, AbsObj(addr, ObjKind.LIST, container_slots=ContainerSlots.empty_sequence()))
    state = state.set_var("alias", AbsVal.from_addrs(frozenset({obj})))
    state = state.set_var("index", AbsVal.top()).set_var("value", AbsVal.from_int(42))
    if operation == "store_alias":
        stmt = IRStoreSubscr(ast.parse("alias[index] = value").body[0])
    elif operation == "attribute_hook":
        stmt = IRLoadAttr(ast.parse("result = unknown_receiver.property").body[0])
    else:
        stmt = IRAstStmt(ast.parse("exec(code)").body[0])
    result = IRInterpreter(None, AnalysisBudget()).transfer(stmt, state, SummaryAccumulator())
    length = result.states[0][1].get_obj(addr).container_slots.length
    assert length.may_contain(0) and length.may_contain(10000)


def test_append_through_alias_cannot_leave_zero_length_as_exact_fact():
    from tests.analysis.legacy_ai_support import solve_source, terminal_state
    final = terminal_state(solve_source("items=[]\nalias=items\nalias.append(42)\nn=len(items)"))
    length = final.get_var("n").get_int_lattice()
    assert length.may_contain(1)
    assert length.get_consts() != frozenset({0})


def test_len_result_survives_independent_summary_boundary(tmp_path):
    from tests.analysis.legacy_ai_support import analyze_program
    with analyze_program(tmp_path, "def size():\n items=[1,2,3]\n return len(items)\nresult=size()\n") as program:
        summary = program.ai.get_summary("subject.size")
        assert 3 in {obj.value for obj in summary.ret if hasattr(obj, "value")}
