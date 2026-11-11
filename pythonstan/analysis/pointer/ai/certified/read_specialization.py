"""Pure ReadOK checking over finite abstract heap alternatives.

This module does not execute Python code, infer alias facts, or authorize a
rewrite.  A trusted caller supplies a sound overapproximation of states at the
actual next operation.  The checker proves a proposed abstract read/stop for
every supplied alternative.  Unknown facts and mismatches retain the exact
residual state, including heap and iterator/continuation identity.

Value tokens denote object identity (never Python ``==``).  Multiple tokens in
a slot are a may-set.  Mutations between next operations are unrestricted;
only the live slot and length at the reached next operation matter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class HeapAlternative:
    """One abstract alternative for the iterated object at a reached next.

    ``exact_builtin_list`` is a proved shape fact, not a guessed annotation.
    ``length_upper=None`` and a missing slot mean unknown.  ``slots`` entries
    contain identity may-sets and must describe every possible object at that
    position.  Token spelling carries no semantics beyond identity.
    """

    list_identity: str
    exact_builtin_list: bool
    length_lower: int
    length_upper: Optional[int]
    slots: Tuple[Tuple[int, frozenset[str]], ...] = ()


@dataclass(frozen=True)
class ResidualState:
    """Opaque saved source state; fallback must resume this exact object.

    The tokens stand for identities in the caller's trusted abstract state.
    The checker never fabricates a replacement heap, frame, or iterator.
    ``continuation_identity`` includes pending exceptions/finally/resume state.
    """

    iterator_identity: str
    list_identity: str
    next_index: int
    exhausted: bool
    heap_identity: str
    locals_identity: str
    continuation_identity: str
    alternatives: Tuple[HeapAlternative, ...]
    exact_builtin_iterator: bool = False


@dataclass(frozen=True)
class ReadProposal:
    iterator_identity: str
    list_identity: str
    next_index: int
    kind: str  # "read" or "stop"
    values: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ReadCheckResult:
    status: str  # "proved", "fallback", "unknown"
    reason: str
    residual_state: ResidualState
    values: frozenset[str] = frozenset()

    @property
    def certified(self) -> bool:
        return self.status == "proved"


def _well_formed(state: ResidualState, proposal: ReadProposal) -> Optional[str]:
    if not isinstance(state, ResidualState) or not isinstance(proposal, ReadProposal):
        return "expected typed abstract state and proposal"
    identities = (state.iterator_identity, state.list_identity, state.heap_identity,
                  state.locals_identity, state.continuation_identity,
                  proposal.iterator_identity, proposal.list_identity)
    if any(type(value) is not str or not value for value in identities):
        return "state and proposal identities must be nonempty strings"
    if (type(state.next_index) is not int or state.next_index < 0
            or type(proposal.next_index) is not int or proposal.next_index < 0
            or type(state.exhausted) is not bool or type(state.exact_builtin_iterator) is not bool):
        return "invalid iterator position/exhaustion fact"
    if type(proposal.kind) is not str or proposal.kind not in {"read", "stop"}:
        return "unsupported proposal kind"
    if type(proposal.values) is not frozenset or any(type(token) is not str or not token for token in proposal.values):
        return "values must be immutable identity-token sets"
    if proposal.kind == "stop" and proposal.values:
        return "a stop proposal cannot carry read values"
    if proposal.kind == "read" and not proposal.values:
        return "a read proposal requires a nonempty value set"
    if type(state.alternatives) is not tuple or not state.alternatives:
        return "at least one sound abstract heap alternative is required"
    for alternative in state.alternatives:
        if not isinstance(alternative, HeapAlternative):
            return "invalid heap alternative"
        if type(alternative.exact_builtin_list) is not bool:
            return "invalid exact-list shape fact"
        if (type(alternative.length_lower) is not int or alternative.length_lower < 0
                or (alternative.length_upper is not None
                    and (type(alternative.length_upper) is not int
                         or alternative.length_upper < alternative.length_lower))):
            return "invalid abstract length interval"
        if type(alternative.slots) is not tuple:
            return "slot facts must be immutable"
        seen = set()
        for slot in alternative.slots:
            if (type(slot) is not tuple or len(slot) != 2 or type(slot[0]) is not int
                    or slot[0] < 0 or slot[0] in seen or type(slot[1]) is not frozenset
                    or not slot[1] or any(type(token) is not str or not token for token in slot[1])):
                return "invalid or duplicate slot fact"
            if alternative.length_upper is not None and slot[0] >= alternative.length_upper:
                return "slot outside every possible live list length"
            seen.add(slot[0])
    return None


def check_list_read(state: ResidualState, proposal: ReadProposal) -> ReadCheckResult:
    """Prove one source next-read implication or retain exact residual state.

    A successful read requires index < length in every alternative and all
    live-slot identity tokens within the proposed set.  A successful stop
    requires an already exhausted iterator or index >= every possible length.
    An iterator stays exhausted even if its original list later grows.
    """
    malformed = _well_formed(state, proposal)
    if malformed:
        return ReadCheckResult("unknown", malformed, state)
    if (proposal.iterator_identity != state.iterator_identity
            or proposal.list_identity != state.list_identity
            or proposal.next_index != state.next_index):
        return ReadCheckResult("fallback", "proposal does not match the current iterator identity and position", state)
    if not state.exact_builtin_iterator:
        return ReadCheckResult("unknown", "exact forward builtin-list iterator semantics are not proved", state)
    if any(alt.list_identity != state.list_identity for alt in state.alternatives):
        return ReadCheckResult("unknown", "heap alternatives do not cover the iterator's established list identity", state)
    if any(not alt.exact_builtin_list for alt in state.alternatives):
        return ReadCheckResult("unknown", "exact builtin-list shape is not proved for every alternative", state)
    if state.exhausted:
        if proposal.kind == "stop":
            return ReadCheckResult("proved", "the same iterator is already exhausted", state)
        return ReadCheckResult("fallback", "an exhausted iterator cannot produce a read", state)
    index = state.next_index
    observed: set[str] = set()
    for alternative in state.alternatives:
        if proposal.kind == "stop":
            if alternative.length_upper is None:
                return ReadCheckResult("unknown", "list length has no proved upper bound", state)
            if index < alternative.length_upper:
                return ReadCheckResult("fallback", "a live alternative may still yield an appended or unread element", state)
        else:
            if index >= alternative.length_lower:
                return ReadCheckResult("fallback", "a live alternative may stop before the proposed read", state)
            values = dict(alternative.slots).get(index)
            if values is None:
                return ReadCheckResult("unknown", "the actual next slot has no complete identity fact", state)
            if not values <= proposal.values:
                return ReadCheckResult("fallback", "the actual next slot may differ from the proposed read", state)
            observed.update(values)
    return ReadCheckResult("proved", "ReadOK holds for every live abstract alternative", state, frozenset(observed))
