"""Independent finite concretizations for domain repair obligations.

These oracles do not call production membership/order functions to decide the
expected sets. They refute coverage bugs and retain useful exact golden cases.
"""
import itertools
import unittest

from pythonstan.analysis.domains.strings import StringDomain, StrLattice
from pythonstan.analysis.domains.ints import IntDomain, IntLattice, Parity


def gamma_str(value, universe):
    return {s for s in universe if value.has_unknown or s in value.consts
            or any(s.startswith(p) for p in value.prefixes)
            or any(s.endswith(t) for t in value.suffixes)
            or any(s.startswith(p) and s.endswith(t) for p, t in value.conjunctions)}


def gamma_int(value, universe):
    if value.is_bottom:
        return set()
    if value.is_top:
        return set(universe)
    if value.consts:
        return set(universe) & set(value.consts)
    return {n for n in universe if (value.lo is None or value.lo <= n)
            and (value.hi is None or n <= value.hi)
            and value.parity != Parity.BOTTOM
            and (value.parity == Parity.TOP or n % 2 == (value.parity == Parity.ODD))}


class ScalarDomainRepair(unittest.TestCase):
    def test_string_finite_laws(self):
        universe = {''.join(x) for size in range(5) for x in itertools.product('abc', repeat=size)}
        domain = StringDomain(max_consts=2)
        values = [StrLattice.bottom(), StrLattice.top()]
        values += [StrLattice.const(s) for s in ('', 'a', 'b', 'ab', 'ac', 'abc')]
        values += [StrLattice.from_prefix(s) for s in ('a', 'ab', 'b')]
        values += [StrLattice.from_suffix(s) for s in ('a', 'ab', 'b')]
        values += [StrLattice._normalize(frozenset(), frozenset(), frozenset(), False,
                                        frozenset({('a', 'b'), ('b', 'c')}))]
        values += [StrLattice.from_consts(frozenset({'a', 'b', 'c'}))]
        for left, right in itertools.product(values, repeat=2):
            a, b = gamma_str(left, universe), gamma_str(right, universe)
            self.assertLessEqual(a | b, gamma_str(domain.join(left, right), universe))
            self.assertLessEqual(a | b, gamma_str(domain.widen(left, right), universe))
            self.assertEqual(a & b, gamma_str(domain.meet(left, right), universe))
            self.assertEqual(a & b, gamma_str(domain.narrow(left, right), universe))
            if domain.leq(left, right):
                self.assertLessEqual(a, b)

    def test_string_isolated_and_nested_patterns(self):
        domain = StringDomain(max_consts=2)
        mixed = StrLattice.from_consts(frozenset({'aa', 'ab', 'z'}))
        widened = domain.widen(StrLattice.bottom(), mixed)
        self.assertLessEqual({'aa', 'ab', 'z'}, gamma_str(widened, {'aa', 'ab', 'z'}))
        self.assertFalse(widened.is_top())
        self.assertEqual(domain.meet(StrLattice.from_prefix('a'), StrLattice.from_prefix('ab')),
                         StrLattice.from_prefix('ab'))
        cross = domain.meet(StrLattice.from_prefix('a'), StrLattice.from_suffix('b'))
        self.assertEqual(gamma_str(cross, {'a', 'ab', 'abc', 'aab', 'b'}), {'ab', 'aab'})
        self.assertIsNone(cross.get_consts())
        many = StrLattice.from_consts(frozenset('abcdefghijk'))
        self.assertEqual(gamma_str(domain.widen(StrLattice.bottom(), many), set('abcdefghijk')),
                         set('abcdefghijk'))

    def test_integer_finite_laws(self):
        universe = set(range(-9, 10))
        domain = IntDomain(max_consts=3)
        values = [IntLattice.bottom(), IntLattice.top()]
        values += [IntLattice.from_consts(frozenset(c)) for size in range(1, 4)
                   for c in itertools.combinations(range(-2, 4), size)]
        values += [IntLattice.from_interval(lo, hi, parity)
                   for lo, hi in ((-2, 2), (0, 3), (None, -1), (1, None), (None, None))
                   for parity in (Parity.TOP, Parity.EVEN, Parity.ODD)]
        for left, right in itertools.product(values, repeat=2):
            a, b = gamma_int(left, universe), gamma_int(right, universe)
            self.assertLessEqual(a | b, gamma_int(domain.join(left, right), universe))
            self.assertLessEqual(a | b, gamma_int(domain.widen(left, right), universe))
            self.assertEqual(a & b, gamma_int(domain.meet(left, right), universe))
            self.assertEqual(a & b, gamma_int(domain.narrow(left, right), universe))
            if domain.leq(left, right):
                self.assertLessEqual(a, b)

    def test_integer_exactness_after_widen(self):
        domain = IntDomain()
        self.assertFalse(domain.leq(IntLattice.const(1), IntLattice.from_consts(frozenset({0, 3}))))
        self.assertEqual(domain.join(IntLattice.const(0), IntLattice.const(3)).get_consts(), frozenset({0, 3}))
        widened = domain.widen(IntLattice.const(0), IntLattice.const(1))
        self.assertIsNone(widened.get_consts())
        self.assertIn(2, gamma_int(widened.refine_neq(0), range(-5, 6)))
        negative = domain.widen(IntLattice.const(-1), IntLattice.const(-2))
        self.assertIn(-3, gamma_int(negative.refine_neq(-1), range(-5, 6)))
        self.assertFalse(IntLattice.from_interval(0, 4, Parity.ODD).may_contain(2))


class SlotMapRepair(unittest.TestCase):
    def setUp(self):
        from pythonstan.analysis.pointer.ai.value_domain import AbsVal
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap, Slot, Presence, SlotMapSemantics
        from pythonstan.analysis.pointer.ai.state import AIState
        self.V, self.Map, self.Slot, self.P = AbsVal, SlotMap, Slot, Presence
        self.Sem, self.State = SlotMapSemantics, AIState

    def test_unknown_read_write_and_default_split(self):
        s = self.Map.for_dict(max_partitions=1)
        a, b, c = (self.V.from_addrs(frozenset({x})) for x in ('A', 'B', 'C'))
        s = s.weak_update_str('known', a).weak_update_str('spilled', b)
        unknown = self.V.bottom().with_component('string', StrLattice.top())
        result = self.Sem(None, None, None).load_item(s, unknown, self.State())
        self.assertEqual(result.result_val.addrs, frozenset({'A', 'B'}))
        self.assertTrue(result.may_be_missing)
        s = s.weak_update_default(c)
        self.assertLessEqual({'A', 'C'}, s.get_str_value('known').addrs)
        split = self.Map.for_dict().weak_update_default(a).weak_update_str('new', b)
        self.assertEqual(split.get_str_value('new').addrs, frozenset({'A', 'B'}))

    def test_presence_join_and_bool_key(self):
        a = self.V.from_addrs(frozenset({'A'}))
        b = self.V.from_addrs(frozenset({'B'}))
        absent = self.Map.for_dict()
        absent.partitions[('str', 'x')] = self.Slot(a, self.P.ABSENT)
        broad = self.Map.for_dict()
        broad.default = self.Slot(b, self.P.MAYBE)
        joined = absent.join(broad)
        self.assertEqual(joined.get_str_presence('x'), self.P.MAYBE)
        self.assertTrue(absent.leq(joined))
        self.assertTrue(broad.leq(joined))
        s = self.Map.for_dict().strong_update(('int', 1), a)
        key = self.V.bottom().with_component('bool', __import__(
            'pythonstan.analysis.pointer.ai.domains.bool', fromlist=['BoolLattice']).BoolLattice.true_())
        result = self.Sem(None, None, None).load_item(s, key, self.State())
        self.assertEqual(result.result_val.addrs, frozenset({'A'}))
        self.assertFalse(result.may_be_missing)


class StateBundleRepair(unittest.TestCase):
    def test_run_local_caps_cover_env_heap_and_interleaving(self):
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.heap import AIAddr, AbsObj, ObjKind, SlotMap
        from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocSite, AllocKind
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        obj = AbstractObject(CallStringContext(), AllocSite('cap_object', AllocKind.OBJECT))
        value = AbsVal.bottom().with_component('string', StrLattice.from_consts(frozenset('abcdefghijk')))
        states = []
        for cap in (10, 20, 50):
            state = AIState(budget=AnalysisBudget(max_const_strings=cap)).set_var('value', value)
            state = state.set_obj(AIAddr.from_kcfa_obj(obj), AbsObj(
                AIAddr.from_kcfa_obj(obj), ObjKind.INSTANCE,
                slots=SlotMap.for_namespace().strong_update_str('value', value)))
            widened = state.widen(state)
            if cap > 10:
                self.assertEqual(widened.get_var('value').get_const_strs(), frozenset('abcdefghijk'))
                read, _ = widened.read_field_value(obj, attr('value'), None, None)
                self.assertEqual(read.get_const_strs(), frozenset('abcdefghijk'))
            else:
                self.assertEqual(gamma_str(widened.get_var('value').get_string_lattice(), set('abcdefghijk')),
                                 set('abcdefghijk'))
            self.assertEqual(widened.registry.get('string').max_consts, cap)
            states.append(widened)
        self.assertEqual(states[1].registry.get('string').max_consts, 20)
        with self.assertRaises((TypeError, AttributeError)):
            states[1].registry.get('string').max_consts = 1
        with self.assertRaises(TypeError):
            states[1].registry.register(StringDomain())
        import pickle
        restored = pickle.loads(pickle.dumps(states[1]))
        self.assertEqual(restored.get_var('value').get_const_strs(), frozenset('abcdefghijk'))

    def test_scalar_tags_and_type_directed_union_reduction(self):
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        string = AIState().set_var('x', AbsVal.from_str('after')).get_var('x')
        self.assertTrue(string.get_int_lattice().is_bottom)
        self.assertTrue(string.get_bool_values().is_bottom())
        self.assertTrue(string.get_bool_lattice().is_true())
        integer = AIState().set_var('x', AbsVal.from_int(7)).get_var('x')
        self.assertEqual(integer.get_int_lattice().get_consts(), frozenset({7}))
        self.assertTrue(integer.get_bool_values().is_bottom())
        self.assertTrue(integer.get_bool_lattice().is_true())
        union = AIState().set_var('x', AbsVal.none_val().join(AbsVal.from_str('x'))).get_var('x')
        self.assertEqual(union.get_const_strs(), frozenset({'x'}))
        self.assertTrue(union.may_be_none)
        self.assertFalse(union.is_bottom())
        mixed = AIState().set_var('x', AbsVal.from_int(7).join(AbsVal.bool_false())).get_var('x')
        self.assertTrue(mixed.get_bool_values().is_false())
        self.assertTrue(mixed.get_bool_lattice().is_top())
        self.assertEqual(mixed.get_int_lattice().get_consts(), frozenset({7}))
        false = AbsVal.bool_false()
        zero = AbsVal.from_int(0)
        self.assertFalse(false.leq(zero))
        self.assertFalse(zero.leq(false))
        self.assertTrue(false.join(zero).get_bool_values().is_false())
        # A raw untagged compatibility constraint product is not exact-kind evidence.
        self.assertIsNone(AbsVal().scalar_kinds)

    def test_scalar_address_key_is_not_an_unknown_key(self):
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.domains.mapslots import KeyDecomposer
        from pythonstan.analysis.pointer.kcfa.object import ConstantObject, AllocSite, AllocKind
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        obj = ConstantObject(CallStringContext(), AllocSite('literal_x', AllocKind.CONSTANT), 'x')
        value = AbsVal.from_addrs(frozenset({obj})).join(AbsVal.from_str('x'))
        tracked, residual, _ = KeyDecomposer().decompose(value, AIState())
        self.assertEqual({key for key, _ in tracked}, {('str', 'x')})
        self.assertFalse(residual)
        class Unknown:
            is_top_value = True
        unknown = Unknown()
        projected = AbsVal.from_addrs(frozenset({unknown}))
        self.assertTrue(projected.addr_top)
        self.assertTrue(projected.get_string_lattice().is_top())
        self.assertTrue(projected.get_int_lattice().is_top)
        self.assertTrue(projected.may_be_none)

    def test_full_state_remap_aliases_cycles_and_future_writes(self):
        import ast
        from pythonstan.ir.ir_statements import IRPass
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.heap import AIAddr, AbsObj, ObjKind, SlotMap, ContainerSlots
        from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocSite, AllocKind
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext, CallSite
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        site = AllocSite('same_allocation', AllocKind.OBJECT)
        receivers = [AbstractObject(CallStringContext((CallSite(IRPass(ast.Pass()), str(n)),), 1), site)
                     for n in range(2)]
        state = AIState(budget=AnalysisBudget(max_addr_set_size=1))
        state = state.set_var('receivers', AbsVal.from_addrs(frozenset(receivers)))
        state.shared_bindings[('closure#1', 'cell')] = AbsVal.from_addrs(frozenset(receivers))
        for i, receiver in enumerate(receivers):
            value = AbsVal.bottom().with_component('string', StrLattice.const(str(i)))
            slots = SlotMap.for_dict().strong_update_str('value', value)
            slots = slots.strong_update(('tuple', (('id', receiver),)), AbsVal.from_addrs(frozenset({receiver})))
            container = ContainerSlots().set_index(0, AbsVal.from_addrs(frozenset(receivers)))
            state = state.set_obj(AIAddr.from_kcfa_obj(receiver), AbsObj(
                AIAddr.from_kcfa_obj(receiver), ObjKind.DICT, slots=slots, container_slots=container))
            state = state.add_overlay_write(receiver, attr('overlay'), value)
        state = state.add_overlay_havoc(receivers[0])
        widened = state.widen(state)
        self.assertEqual(len(widened.get_var('receivers').addrs), 1)
        self.assertEqual(len(widened.heap), 1)
        self.assertEqual(len(widened.overlay), 1)
        summary = next(iter(widened.get_var('receivers').addrs))
        read, _ = widened.read_field_value(summary, attr('value'), None, None)
        self.assertEqual(read.get_const_strs(), frozenset({'0', '1'}))
        self.assertEqual(widened.shared_bindings[('closure#1', 'cell')].addrs, frozenset({summary}))
        heap = widened.get_obj(AIAddr.from_kcfa_obj(summary))
        self.assertIn(('tuple', (('id', summary),)), heap.slots.partitions)
        self.assertEqual(heap.container_slots.get_index(0).addrs, frozenset({summary}))
        self.assertEqual(widened.overlay_havoc, {summary})
        self.assertTrue(state.leq(widened))
        widened = widened.add_overlay_write(receivers[0], attr('later'), AbsVal.from_str('after'))
        read, _ = widened.read_field_value(summary, attr('later'), None, None)
        self.assertEqual(read.get_const_strs(), frozenset({'after'}))
        # The source state and prior aliases have not been mutated.
        self.assertEqual(len(state.heap), 2)
        self.assertEqual(len(state.get_var('receivers').addrs), 2)


if __name__ == '__main__':
    unittest.main()
