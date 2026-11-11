from dataclasses import replace

from pythonstan.analysis.pointer.ai.certified.read_specialization import (
    HeapAlternative, ReadProposal, ResidualState, check_list_read,
)


def state(index=0, values=("a", "b"), exhausted=False):
    alternative = HeapAlternative("list-1", True, len(values), len(values),
                                  tuple((i, frozenset({value})) for i, value in enumerate(values)))
    return ResidualState("iter-1", "list-1", index, exhausted, "heap-current",
                         "locals-current", "continuation-current", (alternative,), True)


def read(index=0, value="a"):
    return ReadProposal("iter-1", "list-1", index, "read", frozenset({value}))


def stop(index):
    return ReadProposal("iter-1", "list-1", index, "stop")


def test_exact_next_slot_is_proved():
    current = state()
    result = check_list_read(current, read())
    assert result.certified
    assert result.values == {"a"}
    assert result.residual_state is current


def test_restored_append_pop_has_same_read_facts():
    # The body may append/pop; at the actual next point the live state is restored.
    current = state(index=1)
    assert check_list_read(current, read(1, "b")).certified


def test_consumed_slot_write_does_not_block_future_read():
    current = state(index=1, values=("changed-consumed-slot", "b"))
    assert check_list_read(current, read(1, "b")).certified


def test_future_slot_write_forces_fallback():
    current = state(index=1, values=("a", "changed"))
    result = check_list_read(current, read(1, "b"))
    assert result.status == "fallback"
    assert result.residual_state is current


def test_shortened_list_cannot_fake_read():
    current = state(index=1, values=("a",))
    assert check_list_read(current, read(1, "b")).status == "fallback"
    assert check_list_read(current, stop(1)).certified


def test_appended_element_cannot_fake_expected_stop():
    current = state(index=2, values=("a", "b", "appended"))
    assert check_list_read(current, stop(2)).status == "fallback"


def test_stop_is_proved_at_actual_exhaustion():
    assert check_list_read(state(index=2), stop(2)).certified


def test_already_exhausted_iterator_stays_exhausted_after_append():
    current = state(index=2, values=("a", "b", "new"), exhausted=True)
    assert check_list_read(current, stop(2)).certified
    assert check_list_read(current, read(2, "new")).status == "fallback"


def test_every_abstract_alternative_must_satisfy_readok():
    current = state(index=1)
    bad = HeapAlternative("list-1", True, 2, 2, ((1, frozenset({"changed"})),))
    current = replace(current, alternatives=current.alternatives + (bad,))
    assert check_list_read(current, read(1, "b")).status == "fallback"
    broad = ReadProposal("iter-1", "list-1", 1, "read", frozenset({"b", "changed"}))
    assert check_list_read(current, broad).certified


def test_subclasses_and_unknown_slot_are_not_proofs():
    current = state()
    subclass = replace(current.alternatives[0], exact_builtin_list=False)
    assert check_list_read(replace(current, alternatives=(subclass,)), read()).status == "unknown"
    unknown = replace(current.alternatives[0], slots=())
    assert check_list_read(replace(current, alternatives=(unknown,)), read()).status == "unknown"


def test_empty_alternatives_are_unknown_not_vacuous_success():
    assert check_list_read(replace(state(), alternatives=()), read()).status == "unknown"


def test_iterating_a_list_does_not_by_itself_prove_builtin_iterator_semantics():
    assert check_list_read(replace(state(), exact_builtin_iterator=False), read()).status == "unknown"


def test_guard_mismatch_keeps_exact_iterator_heap_locals_and_continuation():
    current = state(index=1, values=("changed", "b"))
    result = check_list_read(current, read(0, "a"))
    assert result.status == "fallback"
    assert result.residual_state is current
    assert result.residual_state.next_index == 1
    assert result.residual_state.heap_identity == "heap-current"
    assert result.residual_state.continuation_identity == "continuation-current"


def test_alias_or_iterator_identity_change_cannot_authorize_specialization():
    current = state()
    assert check_list_read(current, replace(read(), list_identity="other")).status == "fallback"
    assert check_list_read(current, replace(read(), iterator_identity="fresh-iterator")).status == "fallback"


def test_unknown_length_and_slot_facts_do_not_prove_end():
    current = state(index=2)
    unknown = replace(current.alternatives[0], length_upper=None)
    assert check_list_read(replace(current, alternatives=(unknown,)), stop(2)).status == "unknown"


def test_early_exit_has_no_obligation_for_unread_future_slot():
    # Only reached reads are submitted. This local proof says nothing about an
    # unread suffix, and cannot be promoted to a whole-loop rewrite certificate.
    current = state(values=("a", "changed-future-slot"))
    assert check_list_read(current, read()).certified


def test_malformed_facts_fail_closed():
    current = state()
    invalid = replace(current.alternatives[0], length_lower=-1)
    assert check_list_read(replace(current, alternatives=(invalid,)), read()).status == "unknown"
    assert check_list_read(current, replace(read(), kind=[])).status == "unknown"
