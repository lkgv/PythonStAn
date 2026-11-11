"""Held-out domain witnesses authored by the independent TEST-01 reviewer.

String expectations use regex languages (including overlapping ends), integer
expectations use Python set arithmetic, and dict expectations use concrete
dictionary operations. Production order/membership functions are not oracles.
"""

import itertools
import re

from pythonstan.analysis.domains.ints import IntDomain, IntLattice, Parity
from pythonstan.analysis.domains.strings import StringDomain, StrLattice


def _string_language(value, universe):
    if value.has_unknown:
        return set(universe)
    patterns = []
    patterns.extend(re.compile(r"\A" + re.escape(p), re.DOTALL) for p in value.prefixes)
    patterns.extend(re.compile(re.escape(s) + r"\Z", re.DOTALL) for s in value.suffixes)
    patterns.extend(re.compile(r"\A(?=" + re.escape(p) + r")(?=.*" + re.escape(s) + r"\Z)",
                               re.DOTALL) for p, s in value.conjunctions)
    return {s for s in universe if s in value.consts or any(rx.search(s) for rx in patterns)}


def _integer_language(value, universe):
    if value.is_bottom:
        return set()
    if value.is_top:
        return set(universe)
    if value.consts:
        return set(value.consts) & set(universe)
    allowed_parities = {Parity.TOP: {0, 1}, Parity.EVEN: {0}, Parity.ODD: {1},
                        Parity.BOTTOM: set()}[value.parity]
    return {n for n in universe if (value.lo is None or value.lo <= n)
            and (value.hi is None or n <= value.hi) and n % 2 in allowed_parities}


def test_string_overlapping_ends_and_unicode_cross_operations():
    domain = StringDomain(max_consts=2)
    universe = {"", "a", "b", "ab", "abb", "aba", "abc", "ababc", "ab\nbc",
                "bc", "bbc", "\n", "α", "αβ", "βα", "α\nβ", "终", "起终", "起中终"}
    prefix = StrLattice.from_prefix
    suffix = StrLattice.from_suffix
    values = [prefix("ab"), suffix("b"), prefix("α"), suffix("β"), prefix("起"), suffix("终"),
              domain.meet(prefix("ab"), suffix("b")),
              domain.meet(prefix("α"), suffix("β")),
              domain.join(prefix("ab"), domain.meet(prefix("b"), suffix("c"))),
              StrLattice.from_consts(frozenset({"α", "终", "\n"}))]
    for a, b in itertools.product(values, repeat=2):
        left, right = _string_language(a, universe), _string_language(b, universe)
        assert _string_language(domain.meet(a, b), universe) == left & right
        assert left | right <= _string_language(domain.widen(a, b), universe)
    overlapping = domain.meet(prefix("ab"), suffix("b"))
    assert "ab" in _string_language(overlapping, universe), "Prefix and suffix may overlap"
    assert "a" not in _string_language(overlapping, universe)


def test_produced_string_facts_cover_all_disjuncts():
    domain = StringDomain()
    value = domain.join(StrLattice.from_prefix("a"), domain.meet(
        StrLattice.from_prefix("b"), StrLattice.from_suffix("c")))
    concrete = {"a", "after", "bc", "boxc"}
    assert concrete <= _string_language(value, concrete)
    for fact in domain.produce_facts("x", value):
        if fact.kind == "has_prefix":
            assert all(any(s.startswith(p) for p in fact.data) for s in concrete), fact
        elif fact.kind == "has_suffix":
            assert all(any(s.endswith(p) for p in fact.data) for s in concrete), fact


def test_string_widen_keeps_useful_common_prefix_and_isolated_empty():
    domain = StringDomain(max_consts=1)
    original = StrLattice.from_consts(frozenset({"api/read", "api/write", ""}))
    result = domain.widen(StrLattice.bottom(), original)
    witnesses = {"api/read", "api/write", "", "api/new", "unrelated"}
    assert {"api/read", "api/write", ""} <= _string_language(result, witnesses)
    assert not result.has_unknown, "Representable common prefix plus one residual must stay useful"
    assert "unrelated" not in _string_language(result, witnesses)


def test_integer_refinement_and_parity_after_widening():
    domain = IntDomain(max_consts=2)
    universe = set(range(-40, 41))
    for constants in ({-11, 5, 17}, {0, 2, 12}, {-18, -4, 8}, {-7, -3, 1}):
        exact = IntLattice.from_consts(frozenset(constants))
        assert _integer_language(exact, universe) == constants
        for bound in (-10, -1, 0, 3, 19):
            for method, predicate in (("refine_lt", lambda n: n < bound),
                                      ("refine_le", lambda n: n <= bound),
                                      ("refine_gt", lambda n: n > bound),
                                      ("refine_ge", lambda n: n >= bound),
                                      ("refine_neq", lambda n: n != bound)):
                expected = {n for n in constants if predicate(n)}
                assert _integer_language(getattr(exact, method)(bound), universe) == expected
    for old, new in ((IntLattice.const(-6), IntLattice.const(-8)),
                     (IntLattice.const(5), IntLattice.const(7))):
        widened = domain.widen(old, new)
        expected = {n for n in universe if n % 2 == next(iter(old.consts)) % 2
                    and (n <= -6 if old.lo < 0 else n >= 5)}
        assert expected <= _integer_language(widened, universe)
        assert widened.get_consts() is None


def test_dictionary_spill_and_unknown_writes_cover_concrete_maps():
    from pythonstan.analysis.pointer.ai.value_domain import AbsVal
    from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, SlotMapSemantics
    from pythonstan.analysis.pointer.ai.state import AIState
    concrete = {"x1": "left", "x2": "middle", "other": "right"}
    slots = SlotMap.for_dict(max_partitions=1)
    for key, value in concrete.items():
        slots = slots.strong_update_str(key, AbsVal.from_addrs(frozenset({value})))
    semantics = SlotMapSemantics(None, None, None)
    state = AIState()
    key = AbsVal.bottom().with_component("string", StrLattice.from_prefix("x"))
    read = semantics.load_item(slots, key, state)
    expected = {value for name, value in concrete.items() if name.startswith("x")}
    assert expected <= read.result_val.addrs
    assert read.may_be_missing is True, "Unbounded prefix permits absent dictionary keys"
    unknown_write = semantics.store_item(slots, key, AbsVal.from_addrs(frozenset({"changed"})), state)
    for chosen in ("x1", "x2"):
        updated = dict(concrete)
        updated[chosen] = "changed"
        for lookup in concrete:
            precise_key = AbsVal.bottom().with_component("string", StrLattice.const(lookup))
            result = semantics.load_item(unknown_write, precise_key, state)
            assert updated[lookup] in result.result_val.addrs, (chosen, lookup, result)
