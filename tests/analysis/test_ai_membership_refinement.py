"""Concrete containment obligations for dictionary guard refinement.

The singleton query in this fixture is a trusted finite-model proof. Production
KCFA currently provides no such proof, so it must retain both heap possibilities.
"""

import ast
import itertools

import pytest

from pythonstan.analysis.pointer.ai.api import AnalysisBudget
from pythonstan.analysis.pointer.ai.domains.conditions import ConditionRefiner
from pythonstan.analysis.pointer.ai.domains.mapslots import DictSemantics, Presence, SlotMap
from pythonstan.analysis.pointer.ai.heap import AIAddr, AbsObj, ObjKind
from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
from pythonstan.analysis.pointer.kcfa.context import CallStringContext
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite


class FiniteModelMembershipProof:
    """External proof for this fixture's explicit finite dictionaries/str keys.

    This test oracle is NOT a production proof implementation. Tests construct
    dictionaries containing only 'a'/'b', no subclass/callback/summary objects.
    """
    def can_refine_dict_membership(self, obj, sig, dict_val, key_val, state):
        return True


def fixture_state(receivers=1, key_names=("a",), query=None):
    objects = tuple(AbstractObject(CallStringContext(), AllocSite(f"dict{i}", AllocKind.DICT))
                    for i in range(receivers))
    state = AIState(pta_query=query, budget=AnalysisBudget())
    for obj in objects:
        slots = SlotMap.for_dict()
        for key in ("a", "b"):
            slots = slots.weak_update_str(key, AbsVal.from_str("value"), Presence.MAYBE)
        addr = AIAddr.from_kcfa_obj(obj)
        state = state.set_obj(addr, AbsObj(addr=addr, kind=ObjKind.DICT, slots=slots))
    state = state.set_var("d", AbsVal.from_addrs(frozenset(objects)))
    state = state.set_var("k", AbsVal.from_strs(frozenset(key_names)))
    return state, objects


def presence(state, obj, key):
    return state.get_obj(AIAddr.from_kcfa_obj(obj)).slots.partitions[("str", key)].pres


def branch_states(state, expression):
    interpreter = IRInterpreter(None, AnalysisBudget())
    state = interpreter._handle_compare_assign(
        "guard", ast.parse(expression, mode="eval").body, state, SummaryAccumulator())
    return ConditionRefiner(AnalysisBudget()).refine(
        state, ast.Name(id="guard", ctx=ast.Load()), is_jump_if_true=True)


@pytest.mark.parametrize("expression", ["k in d", "k not in d", "'a' in d", "'a' not in d"])
def test_positive_and_negative_membership_invert_presence_once(expression):
    state, (obj,) = fixture_state(query=FiniteModelMembershipProof())
    true_state, false_state = branch_states(state, expression)
    expected_true = Presence.ABSENT if "not in" in expression else Presence.PRESENT
    expected_false = Presence.PRESENT if "not in" in expression else Presence.ABSENT
    assert presence(true_state, obj, "a") is expected_true
    assert presence(false_state, obj, "a") is expected_false
    assert presence(state, obj, "a") is Presence.MAYBE


@pytest.mark.parametrize("negated", [False, True])
@pytest.mark.parametrize("receivers,key_names", [(1, ("a",)), (1, ("a", "b")), (2, ("a",)), (2, ("a", "b"))])
@pytest.mark.parametrize("prove_singleton", [False, True])
def test_all_concrete_alias_and_key_choices_survive_the_correct_branch(negated, receivers, key_names, prove_singleton):
    query = FiniteModelMembershipProof() if prove_singleton else None
    state, objects = fixture_state(receivers, key_names, query)
    true_state, false_state = branch_states(state, "k not in d" if negated else "k in d")
    # Enumerate every dictionary-presence valuation and every selected alias/key.
    for bits in itertools.product((False, True), repeat=receivers * 2):
        for selected_obj, selected_key in itertools.product(range(receivers), key_names):
            selected_present = bits[2 * selected_obj + (selected_key == "b")]
            outcome = not selected_present if negated else selected_present
            branch = true_state if outcome else false_state
            for index, obj in enumerate(objects):
                for key_index, key in enumerate(("a", "b")):
                    actual = bits[2 * index + key_index]
                    abstract = presence(branch, obj, key)
                    assert abstract is Presence.MAYBE or actual == (abstract is Presence.PRESENT)


@pytest.mark.parametrize("unknown_part", ["receiver", "key"])
def test_top_address_witnesses_are_not_exhaustive(unknown_part):
    state, (obj,) = fixture_state(query=FiniteModelMembershipProof())
    name = "d" if unknown_part == "receiver" else "k"
    value = state.get_var(name)
    state = state.set_var(name, AbsVal(addrs=value.addrs, components=value.components, addr_top=True))
    for branch in branch_states(state, "k in d"):
        assert presence(branch, obj, "a") is Presence.MAYBE


@pytest.mark.parametrize("response", [False, None, 1, "yes"])
def test_membership_proof_requires_explicit_true(response):
    class Query:
        def can_refine_dict_membership(self, *args):
            return response
    state, (obj,) = fixture_state(query=Query())
    for branch in branch_states(state, "k in d"):
        assert presence(branch, obj, "a") is Presence.MAYBE


def test_missing_heap_information_is_not_materialized_as_an_empty_dictionary():
    state, (obj,) = fixture_state(query=FiniteModelMembershipProof())
    state.heap.clear()
    for branch in branch_states(state, "k in d"):
        assert AIAddr.from_kcfa_obj(obj) not in branch.heap


def test_single_abstract_address_without_uniqueness_proof_keeps_other_concrete_instances():
    state, (obj,) = fixture_state()
    for branch in branch_states(state, "k in d"):
        # The same allocation address can denote both {"a": value} and {}.
        assert presence(branch, obj, "a") is Presence.MAYBE


def test_unknown_predicate_kind_cannot_be_treated_as_negative_membership():
    from pythonstan.analysis.pointer.ai.state import PredicateInfo
    state, (obj,) = fixture_state(query=FiniteModelMembershipProof())
    state = state.add_predicate("guard", PredicateInfo("future_predicate", "d", key_literal="a"))
    for branch in ConditionRefiner(AnalysisBudget()).refine(state, ast.Name(id="guard"), is_jump_if_true=True):
        assert presence(branch, obj, "a") is Presence.MAYBE


@pytest.mark.parametrize("alternative", [AbsVal.from_int(1), AbsVal.bool_true(), AbsVal.none_val(), AbsVal.top()])
def test_production_mixed_scalar_keys_preserve_heap(alternative):
    # Production has no membership-proof provider, even if one component looks
    # like a singleton. Join must never be used as an implicit uniqueness proof.
    state, (obj,) = fixture_state()
    state = state.set_var("k", state.get_var("k").join(alternative))
    for branch in branch_states(state, "k not in d"):
        assert presence(branch, obj, "a") is Presence.MAYBE


def test_singleton_query_alone_does_not_prove_callback_free_membership():
    class SingletonOnly:
        def is_singleton_object(self, obj):
            return True
    state, (obj,) = fixture_state(query=SingletonOnly())
    for branch in branch_states(state, "k in d"):
        assert presence(branch, obj, "a") is Presence.MAYBE


def test_failed_membership_proof_retains_state():
    class FailingProof:
        def can_refine_dict_membership(self, *args):
            raise RuntimeError("no stable snapshot")
    state, (obj,) = fixture_state(query=FailingProof())
    for branch in branch_states(state, "k in d"):
        assert presence(branch, obj, "a") is Presence.MAYBE


def test_source_if_not_in_reaches_correct_ir_branch_presence():
    from pythonstan.analysis.transform.ir import IRTransformer
    from pythonstan.analysis.transform.three_address import ThreeAddressTransformer
    from pythonstan.ir.ir_statements import IRModule, JumpIfFalse

    source = "if k not in d:\n    result = 1\nelse:\n    result = 2\n"
    tree = ast.parse(source)
    tac = ThreeAddressTransformer().visit(tree)
    transformer = IRTransformer(IRModule("guard", "guard.py", tree))
    transformer.process_stmts(tac.body)
    statements = transformer.get_stmts()
    state, (obj,) = fixture_state(query=FiniteModelMembershipProof())
    interpreter = IRInterpreter(None, AnalysisBudget())
    summary = SummaryAccumulator()
    for stmt in statements:
        transfer = interpreter.transfer(stmt, state, summary)
        if isinstance(stmt, JumpIfFalse):
            assert len(transfer.states) == 2
            branches = dict(transfer.states)
            assert presence(branches[None], obj, "a") is Presence.ABSENT
            assert presence(branches[stmt.label], obj, "a") is Presence.PRESENT
            assert stmt.label in statements
            break
        assert len(transfer.states) == 1
        state = transfer.states[0][1]
    else:
        pytest.fail("source guard disappeared before AI")


@pytest.mark.parametrize("target", ["copy", "guard", "d", "k"])
def test_copied_predicate_is_preserved_only_while_operands_are_unchanged(target):
    from pythonstan.analysis.pointer.ai.state import PredicateInfo
    from pythonstan.ir.ir_statements import IRCopy

    state, _ = fixture_state()
    predicate = PredicateInfo("dict_not_contains", "d", key_var="k")
    state = state.set_var("guard", AbsVal.bool_top()).add_predicate("guard", predicate)
    stmt = IRCopy(ast.parse(f"{target} = guard").body[0])
    after = IRInterpreter(None, AnalysisBudget()).transfer(stmt, state, SummaryAccumulator()).states[0][1]
    assert after.get_predicate(target) == (None if target in {"d", "k"} else predicate)
    if target in {"d", "k"}:
        assert after.get_predicate("guard") is None
