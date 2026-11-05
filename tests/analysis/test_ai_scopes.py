"""Tests for scope/closure semantics in AI layer.

Tests for:
- ScopeSemantics: Name resolution for global/nonlocal/cell/local variables
- CellStore: Closure variable storage and lookup
- globals()/locals() builtin evaluation
- Integration with interpreter for closure call resolution
"""

import pytest
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Set


# =============================================================================
# Mock objects for testing
# =============================================================================


@dataclass
class MockAllocSite:
    """Mock alloc site for testing."""
    stmt: str
    kind: str = "OBJECT"


@dataclass
class MockObject:
    """Mock abstract object for testing."""

    name: str
    _kind: str = "FUNCTION"

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if isinstance(other, MockObject):
            return self.name == other.name
        return False

    @property
    def alloc_site(self):
        """Mock alloc_site for AIAddr compatibility."""
        from pythonstan.analysis.pointer.kcfa.object import AllocSite, AllocKind

        kind_map = {
            "FUNCTION": AllocKind.FUNCTION,
            "DICT": AllocKind.DICT,
            "MODULE": AllocKind.MODULE,
            "OBJECT": AllocKind.OBJECT,
        }
        return AllocSite(stmt=f"<mock:{self.name}>", kind=kind_map.get(self._kind, AllocKind.OBJECT))

    @property
    def context(self):
        """Mock context for AIAddr compatibility."""
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        return CallStringContext()

    @property
    def kind(self):
        from pythonstan.analysis.pointer.kcfa.object import AllocKind
        kind_map = {
            "FUNCTION": AllocKind.FUNCTION,
            "DICT": AllocKind.DICT,
            "MODULE": AllocKind.MODULE,
            "OBJECT": AllocKind.OBJECT,
        }
        return kind_map.get(self._kind, AllocKind.OBJECT)


class MockIRScope:
    """Mock IR scope for testing."""

    def __init__(
        self,
        qualname: str = "test_func",
        global_vars: Optional[Set[str]] = None,
        nonlocal_vars: Optional[Set[str]] = None,
        cell_vars: Optional[Set[str]] = None,
    ):
        self.qualname = qualname
        self.global_vars = global_vars or set()
        self.nonlocal_vars = nonlocal_vars or set()
        self.cell_vars = cell_vars or set()

    def get_qualname(self) -> str:
        return self.qualname

    def get_global_vars(self) -> Set[str]:
        return self.global_vars

    def get_nonlocal_vars(self) -> Set[str]:
        return self.nonlocal_vars

    def get_cell_vars(self) -> Set[str]:
        return self.cell_vars


class MockPtaQuery:
    """Mock PTA query interface for testing."""

    def __init__(self):
        self._points_to: Dict[str, FrozenSet[MockObject]] = {}
        self._field_points_to: Dict[tuple, FrozenSet[MockObject]] = {}
        self._cell_vars: Dict[MockObject, Dict[str, FrozenSet[MockObject]]] = {}
        self._global_vars: Dict[MockObject, Dict[str, FrozenSet[MockObject]]] = {}

    def add_points_to(self, var_name: str, objs: FrozenSet[MockObject]):
        """Configure points-to for a variable."""
        self._points_to[var_name] = objs

    def add_field_points_to(
        self, obj: MockObject, field_name: str, objs: FrozenSet[MockObject]
    ):
        """Configure field points-to."""
        key = (obj.name, field_name)
        self._field_points_to[key] = objs

    def add_function_cell_vars(
        self, func_obj: MockObject, bindings: Dict[str, FrozenSet[MockObject]]
    ):
        """Configure cell vars for a function."""
        self._cell_vars[func_obj] = bindings

    def add_function_global_vars(
        self, func_obj: MockObject, bindings: Dict[str, FrozenSet[MockObject]]
    ):
        """Configure global vars for a function."""
        self._global_vars[func_obj] = bindings

    def points_to(self, var, scope, context):
        """Mock points_to query."""
        var_name = var.name if hasattr(var, "name") else str(var)
        return self._points_to.get(var_name, frozenset())

    def field_points_to(self, obj, field, scope, context):
        """Mock field_points_to query."""
        obj_name = obj.name if hasattr(obj, "name") else str(obj)
        field_name = str(field)
        key = (obj_name, field_name)
        return self._field_points_to.get(key, frozenset())

    def get_function_cell_vars(
        self, func_obj: MockObject
    ) -> Dict[str, FrozenSet[MockObject]]:
        """Mock cell vars query."""
        return self._cell_vars.get(func_obj, {})

    def get_function_global_vars(
        self, func_obj: MockObject
    ) -> Dict[str, FrozenSet[MockObject]]:
        """Mock global vars query."""
        return self._global_vars.get(func_obj, {})


# =============================================================================
# Tests for CellStore
# =============================================================================


class TestCellStore:
    """Tests for CellStore closure variable storage."""

    def test_write_and_read_cell(self):
        """Test basic cell write and read."""
        from pythonstan.analysis.pointer.ai.domains.scopes import CellStore
        from pythonstan.analysis.pointer.ai.state import AbsVal

        obj = MockObject("captured_func")
        val = AbsVal.from_addrs(frozenset({obj}))

        store = CellStore()
        store = store.write_cell("x", val, weak=False)

        result = store.read_cell("x")
        assert obj in result.addrs

    def test_read_nonexistent_returns_bottom(self):
        """Test reading nonexistent cell returns bottom."""
        from pythonstan.analysis.pointer.ai.domains.scopes import CellStore
        from pythonstan.analysis.pointer.ai.state import AbsVal

        store = CellStore()
        result = store.read_cell("nonexistent")

        assert result.is_bottom()

    def test_weak_update_joins_values(self):
        """Test weak update joins with existing value."""
        from pythonstan.analysis.pointer.ai.domains.scopes import CellStore
        from pythonstan.analysis.pointer.ai.state import AbsVal

        obj1 = MockObject("func1")
        obj2 = MockObject("func2")
        val1 = AbsVal.from_addrs(frozenset({obj1}))
        val2 = AbsVal.from_addrs(frozenset({obj2}))

        store = CellStore()
        store = store.write_cell("x", val1, weak=False)
        store = store.write_cell("x", val2, weak=True)

        result = store.read_cell("x")
        assert obj1 in result.addrs
        assert obj2 in result.addrs

    def test_parent_chain_resolution(self):
        """Test reading from parent cell store chain."""
        from pythonstan.analysis.pointer.ai.domains.scopes import CellStore
        from pythonstan.analysis.pointer.ai.state import AbsVal

        outer_obj = MockObject("outer_func")
        outer_val = AbsVal.from_addrs(frozenset({outer_obj}))

        parent_store = CellStore()
        parent_store = parent_store.write_cell("outer_var", outer_val, weak=False)

        child_store = CellStore(parent=parent_store)

        # Child should see parent's cell
        result = child_store.read_cell("outer_var")
        assert outer_obj in result.addrs

    def test_write_to_parent(self):
        """Test nonlocal write propagates to parent."""
        from pythonstan.analysis.pointer.ai.domains.scopes import CellStore
        from pythonstan.analysis.pointer.ai.state import AbsVal

        obj1 = MockObject("original")
        obj2 = MockObject("updated")
        val1 = AbsVal.from_addrs(frozenset({obj1}))
        val2 = AbsVal.from_addrs(frozenset({obj2}))

        parent_store = CellStore()
        parent_store = parent_store.write_cell("x", val1, weak=False)

        child_store = CellStore(parent=parent_store)
        child_store, found = child_store.write_cell_to_parent("x", val2, weak=True)

        assert found
        # Parent's cell should now include obj2
        result = child_store.parent.read_cell("x")
        assert obj1 in result.addrs
        assert obj2 in result.addrs

    def test_from_bindings(self):
        """Test creating CellStore from dict of bindings."""
        from pythonstan.analysis.pointer.ai.domains.scopes import CellStore
        from pythonstan.analysis.pointer.ai.state import AbsVal

        obj1 = MockObject("func1")
        obj2 = MockObject("func2")
        bindings = {
            "x": AbsVal.from_addrs(frozenset({obj1})),
            "y": AbsVal.from_addrs(frozenset({obj2})),
        }

        store = CellStore.from_bindings(bindings)

        result_x = store.read_cell("x")
        result_y = store.read_cell("y")
        assert obj1 in result_x.addrs
        assert obj2 in result_y.addrs

    def test_get_all_cells(self):
        """Test getting all cell bindings including parents."""
        from pythonstan.analysis.pointer.ai.domains.scopes import CellStore
        from pythonstan.analysis.pointer.ai.state import AbsVal

        obj1 = MockObject("func1")
        obj2 = MockObject("func2")
        val1 = AbsVal.from_addrs(frozenset({obj1}))
        val2 = AbsVal.from_addrs(frozenset({obj2}))

        parent_store = CellStore()
        parent_store = parent_store.write_cell("outer", val1, weak=False)

        child_store = CellStore(parent=parent_store)
        child_store = child_store.write_cell("inner", val2, weak=False)

        all_cells = child_store.get_all_cells()
        assert "outer" in all_cells
        assert "inner" in all_cells


# =============================================================================
# Tests for ScopeSemantics name resolution
# =============================================================================


class TestScopeSemantics:
    """Tests for ScopeSemantics name resolution."""

    def test_classify_local_name(self):
        """Test local variable classification."""
        from pythonstan.analysis.pointer.ai.domains.scopes import ScopeSemantics, NameKind

        scope_ir = MockIRScope()
        semantics = ScopeSemantics(
            pta_query=None,
            budget=None,
            scope_ir=scope_ir,
        )

        kind = semantics.classify_name("x")
        assert kind == NameKind.LOCAL

    def test_classify_global_name(self):
        """Test global variable classification."""
        from pythonstan.analysis.pointer.ai.domains.scopes import ScopeSemantics, NameKind

        scope_ir = MockIRScope(global_vars={"g"})
        semantics = ScopeSemantics(
            pta_query=None,
            budget=None,
            scope_ir=scope_ir,
        )

        kind = semantics.classify_name("g")
        assert kind == NameKind.GLOBAL

    def test_classify_nonlocal_name(self):
        """Test nonlocal variable classification."""
        from pythonstan.analysis.pointer.ai.domains.scopes import ScopeSemantics, NameKind

        scope_ir = MockIRScope(nonlocal_vars={"nl"})
        semantics = ScopeSemantics(
            pta_query=None,
            budget=None,
            scope_ir=scope_ir,
        )

        kind = semantics.classify_name("nl")
        assert kind == NameKind.NONLOCAL

    def test_classify_cell_name(self):
        """Test cell variable classification."""
        from pythonstan.analysis.pointer.ai.domains.scopes import ScopeSemantics, NameKind

        scope_ir = MockIRScope(cell_vars={"captured"})
        semantics = ScopeSemantics(
            pta_query=None,
            budget=None,
            scope_ir=scope_ir,
        )

        kind = semantics.classify_name("captured")
        assert kind == NameKind.CELL

    def test_read_local_from_env(self):
        """Test reading local variable from env."""
        from pythonstan.analysis.pointer.ai.domains.scopes import ScopeSemantics
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator

        obj = MockObject("local_func")
        val = AbsVal.from_addrs(frozenset({obj}))

        scope_ir = MockIRScope()
        semantics = ScopeSemantics(
            pta_query=None,
            budget=None,
            scope_ir=scope_ir,
        )

        state = AIState().set_var("x", val)
        summary = SummaryAccumulator()

        result = semantics.read_name("x", state, summary)
        assert obj in result.addrs

    def test_read_nonlocal_from_cell_store(self):
        """Test reading nonlocal variable from parent's cell store."""
        from pythonstan.analysis.pointer.ai.domains.scopes import ScopeSemantics, CellStore
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator

        obj = MockObject("outer_func")
        val = AbsVal.from_addrs(frozenset({obj}))

        # Create parent cell store with the variable
        parent_store = CellStore()
        parent_store = parent_store.write_cell("outer_var", val, weak=False)

        # Child scope declares nonlocal outer_var
        scope_ir = MockIRScope(nonlocal_vars={"outer_var"})
        semantics = ScopeSemantics(
            pta_query=None,
            budget=None,
            scope_ir=scope_ir,
            cell_store=CellStore(parent=parent_store),
        )

        state = AIState()
        summary = SummaryAccumulator()

        result = semantics.read_name("outer_var", state, summary)
        assert obj in result.addrs

    def test_write_nonlocal_updates_parent(self):
        """Test writing nonlocal variable updates parent's cell."""
        from pythonstan.analysis.pointer.ai.domains.scopes import ScopeSemantics, CellStore
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator

        obj1 = MockObject("original")
        obj2 = MockObject("updated")
        val1 = AbsVal.from_addrs(frozenset({obj1}))
        val2 = AbsVal.from_addrs(frozenset({obj2}))

        parent_store = CellStore()
        parent_store = parent_store.write_cell("x", val1, weak=False)

        scope_ir = MockIRScope(nonlocal_vars={"x"})
        semantics = ScopeSemantics(
            pta_query=None,
            budget=None,
            scope_ir=scope_ir,
            cell_store=CellStore(parent=parent_store),
        )

        state = AIState()
        summary = SummaryAccumulator()

        # Write to nonlocal
        semantics.write_name("x", val2, state, summary)

        # Read should see both (weak update)
        result = semantics.read_name("x", state, summary)
        assert obj1 in result.addrs
        assert obj2 in result.addrs

    def test_write_cell_updates_both_env_and_store(self):
        """Test writing cell variable updates both env and cell store."""
        from pythonstan.analysis.pointer.ai.domains.scopes import ScopeSemantics
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator

        obj = MockObject("captured_func")
        val = AbsVal.from_addrs(frozenset({obj}))

        scope_ir = MockIRScope(cell_vars={"x"})
        semantics = ScopeSemantics(
            pta_query=None,
            budget=None,
            scope_ir=scope_ir,
        )

        state = AIState()
        summary = SummaryAccumulator()

        new_state = semantics.write_name("x", val, state, summary)

        # Should be in env
        env_val = new_state.get_var("x")
        assert obj in env_val.addrs

        # Should be in cell store
        cell_val = semantics.cell_store.read_cell("x")
        assert obj in cell_val.addrs


# =============================================================================
# Tests for globals()/locals() evaluation
# =============================================================================


class TestGlobalsLocals:
    """Tests for globals() and locals() builtin evaluation."""

    def test_eval_locals_dict_contains_env_vars(self):
        """Test locals() dict contains current env variables."""
        from pythonstan.analysis.pointer.ai.domains.scopes import ScopeSemantics
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget

        obj = MockObject("local_func")
        val = AbsVal.from_addrs(frozenset({obj}))

        budget = AnalysisBudget()
        scope_ir = MockIRScope()
        semantics = ScopeSemantics(
            pta_query=None,
            budget=budget,
            scope_ir=scope_ir,
        )

        state = AIState(budget=budget)
        state = state.set_var("my_var", val)
        summary = SummaryAccumulator()

        result = semantics.eval_locals_dict(state, summary)

        # Should return a dict object address
        assert len(result.addrs) > 0

    def test_eval_globals_dict_returns_dict(self):
        """Test globals() returns a dict-like object."""
        from pythonstan.analysis.pointer.ai.domains.scopes import ScopeSemantics
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.domains.mapslots import SlotMap

        budget = AnalysisBudget()
        scope_ir = MockIRScope(global_vars={"g"})

        # Create globals store with a global var
        globals_store = SlotMap.for_namespace()
        obj = MockObject("global_func")
        val = AbsVal.from_addrs(frozenset({obj}))
        from pythonstan.analysis.pointer.ai.domains.mapslots import make_str_keysig, Presence
        keysig = make_str_keysig("g")
        globals_store = globals_store.strong_update(keysig, val, Presence.PRESENT)

        semantics = ScopeSemantics(
            pta_query=None,
            budget=budget,
            scope_ir=scope_ir,
            globals_store=globals_store,
        )

        state = AIState(budget=budget)
        summary = SummaryAccumulator()

        result = semantics.eval_globals_dict(state, summary)

        # Should return a dict object address
        assert len(result.addrs) > 0

    def test_load_from_scope_dict_const_key(self):
        """Test loading from globals/locals dict with constant key."""
        from pythonstan.analysis.pointer.ai.domains.scopes import ScopeSemantics
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget
        from pythonstan.analysis.pointer.ai.heap import AIAddr, AbsObj, ObjKind
        from pythonstan.analysis.pointer.ai.domains.mapslots import (
            SlotMap, make_str_keysig, Presence
        )
        from pythonstan.analysis.pointer.kcfa.object import AllocSite, AllocKind, AbstractObject
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext

        budget = AnalysisBudget()
        scope_ir = MockIRScope()
        semantics = ScopeSemantics(
            pta_query=None,
            budget=budget,
            scope_ir=scope_ir,
        )

        # Create a dict object with a known key
        func_obj = MockObject("target_func")
        val = AbsVal.from_addrs(frozenset({func_obj}))

        slots = SlotMap.for_namespace()
        keysig = make_str_keysig("target")
        slots = slots.strong_update(keysig, val, Presence.PRESENT)

        alloc_site = AllocSite(stmt="<test_dict>", kind=AllocKind.DICT)
        context = CallStringContext()
        addr = AIAddr(alloc_site=alloc_site, context=context)
        dict_obj_kcfa = AbstractObject(alloc_site=alloc_site, context=context)

        dict_obj = AbsObj(addr=addr, kind=ObjKind.DICT, slots=slots)

        state = AIState(budget=budget)
        state = state.set_obj(addr, dict_obj)
        summary = SummaryAccumulator()

        dict_val = AbsVal.from_addrs(frozenset({dict_obj_kcfa}))
        key_val = AbsVal.from_str("target")

        result = semantics.load_from_scope_dict(
            dict_val, key_val, state, summary, is_globals=True
        )

        assert func_obj in result.addrs


# =============================================================================
# Tests for closure call resolution
# =============================================================================


class TestClosureCallResolution:
    """Tests for closure-aware call target resolution."""

    def test_initialize_closure_from_pta(self):
        """Test initializing cell store from PTA bindings."""
        from pythonstan.analysis.pointer.ai.domains.scopes import ScopeSemantics
        from pythonstan.analysis.pointer.ai.state import AbsVal

        func_obj = MockObject("inner_func", _kind="FUNCTION")
        captured_obj = MockObject("captured_func", _kind="FUNCTION")

        pta_query = MockPtaQuery()
        pta_query.add_function_cell_vars(
            func_obj,
            {"callback": frozenset({captured_obj})}
        )

        semantics = ScopeSemantics(
            pta_query=pta_query,
            budget=None,
            scope_ir=None,
        )

        cell_store = semantics.initialize_closure_from_pta(func_obj)

        # Should have the captured binding
        result = cell_store.read_cell("callback")
        assert captured_obj in result.addrs


# =============================================================================
# Tests for interpreter integration
# =============================================================================


class TestInterpreterIntegration:
    """Tests for interpreter scope semantics integration."""

    def test_interpreter_has_scope_semantics(self):
        """Test interpreter initializes scope semantics."""
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget

        budget = AnalysisBudget()
        scope_ir = MockIRScope(global_vars={"g"}, cell_vars={"c"})

        interpreter = IRInterpreter(
            pta_query=MockPtaQuery(),
            budget=budget,
            scope_ir=scope_ir,
        )

        assert interpreter._scope_semantics is not None
        assert "g" in interpreter._scope_semantics._global_vars
        assert "c" in interpreter._scope_semantics._cell_vars

    def test_read_name_method(self):
        """Test interpreter _read_name helper."""
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget

        budget = AnalysisBudget()
        scope_ir = MockIRScope()

        interpreter = IRInterpreter(
            pta_query=MockPtaQuery(),
            budget=budget,
            scope_ir=scope_ir,
        )

        obj = MockObject("my_func")
        val = AbsVal.from_addrs(frozenset({obj}))
        state = AIState(budget=budget).set_var("x", val)
        summary = SummaryAccumulator()

        result = interpreter._read_name("x", state, summary)
        assert obj in result.addrs

    def test_write_name_method(self):
        """Test interpreter _write_name helper."""
        from pythonstan.analysis.pointer.ai.interpreter import IRInterpreter
        from pythonstan.analysis.pointer.ai.state import AIState, AbsVal
        from pythonstan.analysis.pointer.ai.summary import SummaryAccumulator
        from pythonstan.analysis.pointer.ai.api import AnalysisBudget

        budget = AnalysisBudget()
        scope_ir = MockIRScope()

        interpreter = IRInterpreter(
            pta_query=MockPtaQuery(),
            budget=budget,
            scope_ir=scope_ir,
        )

        obj = MockObject("my_func")
        val = AbsVal.from_addrs(frozenset({obj}))
        state = AIState(budget=budget)
        summary = SummaryAccumulator()

        new_state = interpreter._write_name("x", val, state, summary)

        result = new_state.get_var("x")
        assert obj in result.addrs


# =============================================================================
# Tests for PtaQueryAdapter extensions
# =============================================================================


class TestPtaQueryAdapterExtensions:
    """Tests for PtaQueryAdapter cell/global var methods."""

    def test_get_function_cell_vars_exists(self):
        """Test PtaQueryAdapter has get_function_cell_vars method."""
        from pythonstan.analysis.pointer.kcfa.pta_query_adapter import PtaQueryAdapter
        
        assert hasattr(PtaQueryAdapter, 'get_function_cell_vars')

    def test_get_function_global_vars_exists(self):
        """Test PtaQueryAdapter has get_function_global_vars method."""
        from pythonstan.analysis.pointer.kcfa.pta_query_adapter import PtaQueryAdapter
        
        assert hasattr(PtaQueryAdapter, 'get_function_global_vars')


# =============================================================================
# Tests for PtaQuery protocol extensions
# =============================================================================


class TestPtaQueryProtocolExtensions:
    """Tests for PtaQuery protocol cell/global var methods."""

    def test_protocol_has_cell_vars_method(self):
        """Test PtaQuery protocol includes get_function_cell_vars."""
        from pythonstan.analysis.pointer.ai.api import PtaQuery
        import inspect
        
        # Check the protocol has the method
        methods = [name for name, _ in inspect.getmembers(PtaQuery, predicate=inspect.isfunction)]
        assert 'get_function_cell_vars' in methods or hasattr(PtaQuery, 'get_function_cell_vars')

    def test_protocol_has_global_vars_method(self):
        """Test PtaQuery protocol includes get_function_global_vars."""
        from pythonstan.analysis.pointer.ai.api import PtaQuery
        import inspect
        
        methods = [name for name, _ in inspect.getmembers(PtaQuery, predicate=inspect.isfunction)]
        assert 'get_function_global_vars' in methods or hasattr(PtaQuery, 'get_function_global_vars')
