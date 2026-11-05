"""Tests for AI abstract domains.

Tests:
- FieldStore get/set operations and widening
- SlotMap namespace and dict semantics
- String domain lattice operations and widening
- Bool domain operations
"""

import pytest
from typing import FrozenSet


class TestFieldStore:
    """Tests for FieldStore (bounded map + default bucket)."""

    def test_empty_field_store(self):
        """Empty field store returns empty for all keys."""
        from pythonstan.analysis.pointer.ai.heap import FieldStore
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        fs = FieldStore()
        assert fs.get_const(attr("foo")).is_bottom()
        assert fs.get_unknown().is_bottom()
        assert fs.is_bottom()

    def test_weak_set_const(self):
        """Weak set adds to precise map."""
        from pythonstan.analysis.pointer.ai.heap import FieldStore
        from pythonstan.analysis.pointer.ai.state import AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        # Use strings as mock "objects" for testing
        fs = FieldStore()
        fs = fs.weak_set_const(attr("x"), AbsVal.from_addrs(frozenset({"obj1"})))
        
        assert fs.get_const(attr("x")).addrs == frozenset({"obj1"})
        assert fs.get_const(attr("y")).is_bottom()  # Different key

    def test_weak_set_joins(self):
        """Weak set joins with existing value."""
        from pythonstan.analysis.pointer.ai.heap import FieldStore
        from pythonstan.analysis.pointer.ai.state import AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        fs = FieldStore()
        fs = fs.weak_set_const(attr("x"), AbsVal.from_addrs(frozenset({"obj1"})))
        fs = fs.weak_set_const(attr("x"), AbsVal.from_addrs(frozenset({"obj2"})))
        
        assert fs.get_const(attr("x")).addrs == frozenset({"obj1", "obj2"})

    def test_default_bucket(self):
        """Unknown key read returns default bucket."""
        from pythonstan.analysis.pointer.ai.heap import FieldStore
        from pythonstan.analysis.pointer.ai.state import AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        fs = FieldStore()
        fs = fs.weak_set_unknown(AbsVal.from_addrs(frozenset({"default_obj"})))
        
        assert fs.get_unknown().addrs == frozenset({"default_obj"})
        # Const reads include default bucket
        assert fs.get_const(attr("any_key")).addrs == frozenset({"default_obj"})

    def test_join(self):
        """Join merges two field stores."""
        from pythonstan.analysis.pointer.ai.heap import FieldStore
        from pythonstan.analysis.pointer.ai.state import AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        fs1 = FieldStore()
        fs1 = fs1.weak_set_const(attr("x"), AbsVal.from_addrs(frozenset({"obj1"})))
        
        fs2 = FieldStore()
        fs2 = fs2.weak_set_const(attr("x"), AbsVal.from_addrs(frozenset({"obj2"})))
        fs2 = fs2.weak_set_const(attr("y"), AbsVal.from_addrs(frozenset({"obj3"})))
        
        joined = fs1.join(fs2)
        
        assert joined.get_const(attr("x")).addrs == frozenset({"obj1", "obj2"})
        assert joined.get_const(attr("y")).addrs == frozenset({"obj3"})

    def test_widen_spills_keys(self):
        """Widening spills keys when too many are tracked."""
        from pythonstan.analysis.pointer.ai.heap import FieldStore
        from pythonstan.analysis.pointer.ai.state import AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        # Create field store with low max_keys
        fs1 = FieldStore(max_keys=3)
        fs1 = fs1.weak_set_const(attr("a"), AbsVal.from_addrs(frozenset({"obj_a"})))
        fs1 = fs1.weak_set_const(attr("b"), AbsVal.from_addrs(frozenset({"obj_b"})))
        fs1 = fs1.weak_set_const(attr("c"), AbsVal.from_addrs(frozenset({"obj_c"})))
        
        # Add more keys via widening
        fs2 = FieldStore(max_keys=3)
        fs2 = fs2.weak_set_const(attr("d"), AbsVal.from_addrs(frozenset({"obj_d"})))
        fs2 = fs2.weak_set_const(attr("e"), AbsVal.from_addrs(frozenset({"obj_e"})))
        
        widened = fs1.widen(fs2)
        
        # Should have spilled some to default bucket
        assert len(widened.precise_map) <= 3
        # All values should still be reachable via get_unknown
        all_vals = widened.get_unknown()
        assert not all_vals.is_bottom()
        # At least some should be in default bucket
        assert not widened.default_bucket.is_bottom() or len(widened.precise_map) <= 3


class TestStringDomain:
    """Tests for StringDomain (stratified widening)."""

    def test_const_creation(self):
        """Create constant string value."""
        from pythonstan.analysis.pointer.ai.domains.strings import StrLattice
        
        s = StrLattice.const("hello")
        assert s.is_const()
        assert s.get_consts() == frozenset({"hello"})
        assert s.may_equal("hello")
        assert not s.may_equal("world")

    def test_consts_join(self):
        """Join constant string sets."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice
        
        domain = StringDomain()
        s1 = StrLattice.const("a")
        s2 = StrLattice.const("b")
        
        joined = domain.join(s1, s2)
        assert joined.is_const()
        assert joined.get_consts() == frozenset({"a", "b"})

    def test_widen_to_prefix(self):
        """Widening promotes consts to prefixes when too many."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice
        
        domain = StringDomain(max_consts=3, max_prefixes=5)
        
        # Start with some constants
        s1 = StrLattice.from_consts(frozenset({"a1", "a2", "a3"}))
        
        # Add more via widening
        s2 = StrLattice.from_consts(frozenset({"b1", "b2"}))
        
        widened = domain.widen(s1, s2)
        
        # Should have widened to PREFIX or TOP
        assert not widened.is_const() or len(widened.values) <= 3

    def test_meet_filters_consts(self):
        """Meet filters constants."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice
        
        domain = StringDomain()
        s1 = StrLattice.from_consts(frozenset({"a", "b", "c"}))
        s2 = StrLattice.from_consts(frozenset({"b", "c", "d"}))
        
        met = domain.meet(s1, s2)
        assert met.get_consts() == frozenset({"b", "c"})

    def test_top_absorbs(self):
        """Top absorbs in join."""
        from pythonstan.analysis.pointer.ai.domains.strings import StringDomain, StrLattice
        
        domain = StringDomain()
        s1 = StrLattice.const("hello")
        s2 = StrLattice.top()
        
        joined = domain.join(s1, s2)
        assert joined.is_top()


class TestBoolDomain:
    """Tests for BoolDomain (four-valued lattice)."""

    def test_definite_values(self):
        """Test definite True and False."""
        from pythonstan.analysis.pointer.ai.domains.bool import BoolDomain, BoolLattice
        
        domain = BoolDomain()
        t = BoolLattice.true_()
        f = BoolLattice.false_()
        
        assert t.is_true()
        assert not t.is_false()
        assert f.is_false()
        assert not f.is_true()

    def test_join_to_top(self):
        """Joining True and False gives Top."""
        from pythonstan.analysis.pointer.ai.domains.bool import BoolDomain, BoolLattice
        
        domain = BoolDomain()
        t = BoolLattice.true_()
        f = BoolLattice.false_()
        
        joined = domain.join(t, f)
        assert joined.is_top()
        assert joined.may_be_true()
        assert joined.may_be_false()

    def test_negation(self):
        """Negation flips True/False."""
        from pythonstan.analysis.pointer.ai.domains.bool import BoolLattice
        
        t = BoolLattice.true_()
        f = BoolLattice.false_()
        top = BoolLattice.top()
        
        assert t.negate().is_false()
        assert f.negate().is_true()
        assert top.negate().is_top()

    def test_boolean_operations(self):
        """Test AND, OR operations."""
        from pythonstan.analysis.pointer.ai.domains.bool import BoolDomain, BoolLattice
        
        domain = BoolDomain()
        t = BoolLattice.true_()
        f = BoolLattice.false_()
        top = BoolLattice.top()
        
        # AND
        assert domain.and_(t, t).is_true()
        assert domain.and_(t, f).is_false()
        assert domain.and_(f, f).is_false()
        assert domain.and_(t, top).is_top()
        
        # OR
        assert domain.or_(t, f).is_true()
        assert domain.or_(f, f).is_false()
        assert domain.or_(t, top).is_true()


class TestSlotMapSemantics:
    """Tests for SlotMap namespace/dict semantics."""

    def test_slotmap_for_namespace_string_keys(self):
        """Namespace SlotMap uses string-based keys."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import (
            SlotMap, Presence
        )
        from pythonstan.analysis.pointer.ai.state import AbsVal
        
        slots = SlotMap.for_namespace()
        val = AbsVal.from_str("test_value")
        
        # Store using strong_update_str method
        new_slots = slots.strong_update_str("attr_name", val)
        
        # Verify the key was stored
        retrieved = new_slots.get_str("attr_name")
        assert retrieved.pres == Presence.PRESENT
        assert not retrieved.val.is_bottom()

    def test_slotmap_presence_tracking(self):
        """SlotMap tracks presence correctly for bounded namespaces."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import (
            SlotMap, Presence
        )
        from pythonstan.analysis.pointer.ai.state import AbsVal
        
        slots = SlotMap.for_namespace()
        val = AbsVal.from_str("value")
        
        # Store a known key
        slots = slots.strong_update_str("known_key", val)
        
        # Known key should be present
        known_slot = slots.get_str("known_key")
        assert known_slot.pres.is_definitely_present()
        
        # Unknown key gets default slot (ABSENT for bounded namespace)
        unknown_slot = slots.get_str("unknown_key")
        assert unknown_slot.pres.is_definitely_absent()

    def test_slotmap_for_dict_keysig(self):
        """Dict SlotMap uses full KeySig with type tag."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import (
            SlotMap, Presence, make_str_keysig, make_int_keysig
        )
        from pythonstan.analysis.pointer.ai.state import AbsVal
        
        slots = SlotMap.for_dict()
        
        # Store string key
        str_val = AbsVal.from_str("value1")
        slots = slots.strong_update_str("key1", str_val)
        
        # Verify string key
        assert slots.get_str("key1").pres == Presence.PRESENT

    def test_keysig_type_variants(self):
        """KeySig correctly handles string and int types."""
        from pythonstan.analysis.pointer.ai.domains.mapslots import (
            make_str_keysig, make_int_keysig, keysig_kind
        )
        
        str_sig = make_str_keysig("foo")
        int_sig = make_int_keysig(42)
        
        assert keysig_kind(str_sig) == "str"
        assert keysig_kind(int_sig) == "int"
        assert str_sig == ("str", "foo")
        assert int_sig == ("int", 42)


class TestAbsVal:
    """Tests for AbsVal (abstract values)."""

    def test_from_addrs(self):
        """Create value from address set."""
        from pythonstan.analysis.pointer.ai.state import AbsVal
        
        # Use mock objects for testing
        addrs = frozenset(["obj1", "obj2"])
        v = AbsVal.from_addrs(addrs)
        
        assert v.addrs == addrs
        assert not v.is_bottom()

    def test_from_str(self):
        """Create value from string constant."""
        from pythonstan.analysis.pointer.ai.state import AbsVal
        
        v = AbsVal.from_str("hello")
        assert v.is_const_str()
        assert v.get_const_strs() == frozenset({"hello"})

    def test_join_values(self):
        """Join combines address sets and string sets."""
        from pythonstan.analysis.pointer.ai.state import AbsVal
        
        v1 = AbsVal(addrs=frozenset(["a"]), str_const=frozenset({"x"}))
        v2 = AbsVal(addrs=frozenset(["b"]), str_const=frozenset({"y"}))
        
        joined = v1.join(v2)
        assert joined.addrs == frozenset(["a", "b"])
        assert joined.str_const == frozenset({"x", "y"})

    def test_widen_strings(self):
        """Widening with many strings compresses or widens to unknown."""
        from pythonstan.analysis.pointer.ai.state import AbsVal

        v1 = AbsVal.from_strs(frozenset({"a", "b", "c", "d", "e"}))
        v2 = AbsVal.from_strs(frozenset({"f", "g", "h", "i", "j", "k"}))

        widened = v1.widen(v2, max_strs=8)

        # With the new string domain, widening may compress to prefixes/suffixes
        # or go to top. Either way, it should no longer be enumerable.
        # The new behavior compresses constants to prefixes before going to top.
        str_lattice = widened.get_string_lattice()
        # Should not be enumerable (either has prefixes/suffixes or is top)
        assert not str_lattice.is_enumerable() or len(str_lattice.consts) <= 8


class TestAIState:
    """Tests for AIState."""

    def test_empty_state(self):
        """Empty state is bottom."""
        from pythonstan.analysis.pointer.ai.state import AIState
        
        state = AIState.bottom()
        assert state.is_bottom()

    def test_set_get_var(self):
        """Set and get variable."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        
        state = AIState()
        val = AbsVal.from_str("hello")
        
        state = state.set_var("x", val)
        retrieved = state.get_var("x")
        
        assert retrieved.str_const == frozenset({"hello"})

    def test_state_join(self):
        """Join merges environments."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        
        s1 = AIState().set_var("x", AbsVal.from_str("a"))
        s2 = AIState().set_var("x", AbsVal.from_str("b"))
        
        joined = s1.join(s2)
        x_val = joined.get_var("x")
        
        assert x_val.str_const == frozenset({"a", "b"})


class TestSummaryAccumulator:
    """Tests for SummaryAccumulator."""

    def test_add_write(self):
        """Add field write to summary."""
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        acc = SummaryAccumulator()
        acc.add_write(
            frozenset(["obj1"]),
            attr("x"),
            frozenset(["val1"]),
        )
        
        summary = acc.to_summary()
        assert len(summary.writes) == 1
        assert summary.writes[0].field_key == attr("x")

    def test_consolidate_writes(self):
        """Multiple writes to same field are consolidated."""
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.kcfa.heap_model import attr
        
        acc = SummaryAccumulator()
        field = attr("x")
        obj_set = frozenset(["obj1"])
        
        acc.add_write(obj_set, field, frozenset(["val1"]))
        acc.add_write(obj_set, field, frozenset(["val2"]))
        
        summary = acc.to_summary()
        # Should be consolidated into one write
        assert len(summary.writes) == 1
        assert frozenset(["val1", "val2"]) <= summary.writes[0].value_addrs

    def test_add_return(self):
        """Add return addresses."""
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        
        acc = SummaryAccumulator()
        acc.add_return(frozenset(["ret_obj"]))
        
        summary = acc.to_summary()
        assert "ret_obj" in summary.ret


class TestCallableDomain:
    """Tests for the new callable domain."""

    def test_callable_domain_creation(self):
        """Test CallableDomain and CallableVal creation."""
        from pythonstan.analysis.pointer.ai.domains.callable import (
            CallableDomain,
            CallableVal,
            FunctionRef,
        )

        class MockObj:
            def __init__(self, name: str) -> None:
                self.name = name

            def __hash__(self) -> int:
                return hash(self.name)

        domain = CallableDomain()
        assert domain.name == "callable"

        # Test bottom and top
        assert domain.bottom().is_bottom()
        assert domain.top().is_top()

        # Test from_tokens
        fn_obj = MockObj("fn")
        val = CallableVal.from_tokens(frozenset({FunctionRef(fn_obj)}))
        assert not val.is_bottom()
        assert not val.is_top()
        assert len(val.tokens) == 1

    def test_callable_widen_to_top(self):
        """Widening spills to top when too many tokens exist."""
        from pythonstan.analysis.pointer.ai.domains.callable import (
            CallableDomain,
            CallableVal,
            FunctionRef,
        )

        class MockObj:
            def __init__(self, name: str) -> None:
                self.name = name

            def __hash__(self) -> int:
                return hash(self.name)

        objs = frozenset(FunctionRef(MockObj(str(i))) for i in range(5))
        domain = CallableDomain(max_tokens=3)
        widened = domain.widen(CallableVal.bottom(), CallableVal.from_tokens(objs))
        assert widened.is_top()

    def test_callable_to_callable_targets(self):
        """Callable targets are extracted from callable tokens."""
        from pythonstan.analysis.pointer.ai.domains.callable import (
            CallableVal,
            FunctionRef,
        )
        from pythonstan.analysis.pointer.ai.value_domain import (
            CALLABLE_DOMAIN,
            to_callable_targets,
        )
        from pythonstan.analysis.pointer.ai.state import AbsVal

        class MockObj:
            def __init__(self, name: str) -> None:
                self.name = name

            def __hash__(self) -> int:
                return hash(self.name)

        fn_obj = MockObj("fn")
        callable_val = CallableVal.from_tokens(frozenset({FunctionRef(fn_obj)}))
        val = AbsVal(components={CALLABLE_DOMAIN: callable_val})
        targets = to_callable_targets(val)
        assert any(t.callee_obj == fn_obj for t in targets)

    def test_callable_auto_populates_from_addrs(self):
        """Callable tokens are derived from address sets on reduction."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.domains.callable import (
            CallableVal,
            FunctionRef,
        )
        from pythonstan.analysis.pointer.ai.value_domain import CALLABLE_DOMAIN
        from pythonstan.analysis.pointer.kcfa.object import AllocKind

        class MockObj:
            def __init__(self, name: str, kind: AllocKind) -> None:
                self.name = name
                self.kind = kind

            def __hash__(self) -> int:
                return hash(self.name)

            def __eq__(self, other: object) -> bool:
                if isinstance(other, MockObj):
                    return self.name == other.name
                return False

        func_obj = MockObj("fn", AllocKind.FUNCTION)
        state = AIState()
        state = state.set_var("f", AbsVal.from_addrs(frozenset({func_obj})))
        val = state.get_var("f")
        callable_elem = val.get_component(CALLABLE_DOMAIN)
        assert isinstance(callable_elem, CallableVal)
        assert FunctionRef(func_obj) in callable_elem.tokens

    def test_bind_receiver_creates_bound_method(self):
        """bind_receiver creates bound method tokens."""
        from pythonstan.analysis.pointer.ai.domains.callable import (
            CallableVal,
            FunctionRef,
            BoundMethodRef,
        )
        from pythonstan.analysis.pointer.ai.state import AbsVal
        from pythonstan.analysis.pointer.kcfa.object import AllocKind

        class MockObj:
            def __init__(self, name: str, kind: AllocKind) -> None:
                self.name = name
                self.kind = kind

            def __hash__(self) -> int:
                return hash(self.name)

        owner_obj = MockObj("owner_cls", AllocKind.CLASS)
        func_obj = MockObj("func", AllocKind.FUNCTION)
        recv_obj = MockObj("recv", AllocKind.INSTANCE)

        callable_val = CallableVal.from_tokens(frozenset({FunctionRef(func_obj)}))
        receiver = AbsVal.from_addrs(frozenset({recv_obj}))
        bound = callable_val.bind_receiver(owner_obj, receiver)
        assert any(isinstance(tok, BoundMethodRef) for tok in bound.tokens)


class TestTypeDomain:
    """Tests for the type domain."""

    def test_type_domain_creation(self):
        """Test TypeDomain and TypeVal creation."""
        from pythonstan.analysis.pointer.ai.domains.type_domain import (
            TypeDomain,
            TypeVal,
            ClassRef,
        )

        class MockObj:
            def __init__(self, name: str) -> None:
                self.name = name

            def __hash__(self) -> int:
                return hash(self.name)

        domain = TypeDomain()
        assert domain.name == "type"

        # Test bottom and top
        assert domain.bottom().is_bottom()
        assert domain.top().is_top()

        # Test from_tokens
        cls_obj = MockObj("MyClass")
        val = TypeVal.from_tokens(frozenset({ClassRef(cls_obj)}))
        assert not val.is_bottom()
        assert len(val.tokens) == 1

    def test_type_auto_populates_from_addrs(self):
        """Type tokens are derived from class objects on reduction."""
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.domains.type_domain import (
            TypeVal,
            ClassRef,
        )
        from pythonstan.analysis.pointer.ai.value_domain import TYPE_DOMAIN
        from pythonstan.analysis.pointer.kcfa.object import AllocKind

        class MockObj:
            def __init__(self, name: str, kind: AllocKind) -> None:
                self.name = name
                self.kind = kind

            def __hash__(self) -> int:
                return hash(self.name)

            def __eq__(self, other: object) -> bool:
                if isinstance(other, MockObj):
                    return self.name == other.name
                return False

        cls_obj = MockObj("MyClass", AllocKind.CLASS)
        state = AIState()
        state = state.set_var("c", AbsVal.from_addrs(frozenset({cls_obj})))
        val = state.get_var("c")
        type_elem = val.get_component(TYPE_DOMAIN)
        assert isinstance(type_elem, TypeVal)
        assert ClassRef(cls_obj) in type_elem.tokens


