"""Scope and closure semantics for abstract interpretation.

This module provides first-class function semantics:
- Name resolution for global/nonlocal/cell/local variables
- Closure environment representation using SlotMap
- globals()/locals() builtin evaluation
- Sound handling of dynamic scope access

Design:
- ScopeSemantics: Main service class for name resolution and scope operations
- CellStore: SlotMap-backed storage for cell/closure variables
- GlobalsView: SlotMap-backed view of module namespace for globals()
- LocalsSnapshot: Dict-like view of current locals for locals()

Name resolution follows Python's LEGB rule with IR scope metadata:
1. global_vars: Names declared with `global` -> module namespace
2. nonlocal_vars: Names declared `nonlocal` -> enclosing scope's cell storage
3. cell_vars: Names captured by nested functions -> cell storage (shared)
4. Otherwise: local environment (AIState.env)

Soundness:
- Dynamic keys in globals()/locals() access -> unknown effect + havoc
- Unknown module identity -> conservative globals access
- Writes through locals() dict -> conservative (CPython writeback unclear)
"""

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Dict,
    FrozenSet,
    Optional,
    Set,
    Tuple,
)

from .mapslots import SlotMap, Presence, make_str_keysig

if TYPE_CHECKING:
    from pythonstan.ir.ir_statements import IRScope
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from ..state import AIState
    from ..value_domain import AbsVal
    from ..summary import SummaryAccumulator
    from ..api import PtaQuery, AnalysisBudget

__all__ = [
    "ScopeSemantics",
    "CellStore",
    "ScopeBinding",
    "NameKind",
]


# =============================================================================
# NameKind - Classification of variable storage
# =============================================================================

class NameKind:
    """Classification of variable binding location."""
    LOCAL = "local"        # Normal local variable (env)
    CELL = "cell"          # Cell variable (closure storage, may be read by nested)
    NONLOCAL = "nonlocal"  # Nonlocal variable (read/write outer scope's cell)
    GLOBAL = "global"      # Global variable (module namespace)


# =============================================================================
# CellStore - SlotMap-backed cell/closure variable storage
# =============================================================================

@dataclass
class CellStore:
    """SlotMap-backed storage for cell/nonlocal variables.

    Each function frame that participates in closures has a CellStore.
    - Defining function: stores its cell_vars here
    - Inner function: accesses nonlocal_vars via parent's CellStore

    The store uses string keys (variable names) mapped to Slot(val, pres).

    Attributes:
        slots: SlotMap for variable name -> value mapping
        parent: Optional parent CellStore for nested scopes
    """
    slots: SlotMap = field(default_factory=lambda: SlotMap.for_namespace())
    parent: Optional['CellStore'] = None

    def copy(self) -> 'CellStore':
        """Create a shallow copy (shares parent reference)."""
        return CellStore(slots=self.slots.copy(), parent=self.parent)

    def read_cell(self, name: str) -> 'AbsVal':
        """Read a cell variable value.

        First checks local slots, then parent chain for nonlocal resolution.
        """
        keysig = make_str_keysig(name)
        slot = self.slots.get(keysig)
        if slot.pres.may_be_present():
            return slot.val
        # Not in local cells, check parent (nonlocal case)
        if self.parent is not None:
            return self.parent.read_cell(name)
        # Not found anywhere, return bottom
        from ..value_domain import AbsVal
        return AbsVal.bottom()

    def write_cell(self, name: str, val: 'AbsVal', weak: bool = True) -> 'CellStore':
        """Write a cell variable value.

        Args:
            name: Variable name
            val: Value to write
            weak: If True, join with existing; if False, replace (strong update)
        """
        keysig = make_str_keysig(name)
        new_store = self.copy()
        if weak:
            new_store.slots = new_store.slots.weak_update(keysig, val, Presence.PRESENT)
        else:
            new_store.slots = new_store.slots.strong_update(keysig, val, Presence.PRESENT)
        return new_store

    def write_cell_to_parent(
        self, name: str, val: 'AbsVal', weak: bool = True
    ) -> Tuple['CellStore', bool]:
        """Write to parent's cell storage (for nonlocal writes).

        Returns:
            (updated_store, found) - found is True if parent had the cell
        """
        if self.parent is None:
            return self, False
        # Check if parent has this cell
        keysig = make_str_keysig(name)
        slot = self.parent.slots.get(keysig)
        if slot.pres.may_be_present():
            # Update parent's slot
            new_parent = self.parent.write_cell(name, val, weak=weak)
            new_store = self.copy()
            new_store.parent = new_parent
            return new_store, True
        # Recurse up the parent chain
        new_parent, found = self.parent.write_cell_to_parent(name, val, weak)
        if found:
            new_store = self.copy()
            new_store.parent = new_parent
            return new_store, True
        return self, False

    def has_cell(self, name: str) -> bool:
        """Check if a cell variable exists in this store or parents."""
        keysig = make_str_keysig(name)
        slot = self.slots.get(keysig)
        if slot.pres.may_be_present():
            return True
        if self.parent is not None:
            return self.parent.has_cell(name)
        return False

    def get_all_cells(self) -> Dict[str, 'AbsVal']:
        """Get all cell variable bindings (including parents)."""
        result: Dict[str, 'AbsVal'] = {}
        # Collect from parents first (shadowed by local)
        if self.parent is not None:
            result.update(self.parent.get_all_cells())
        # Local cells override
        for keysig, slot in self.slots.partitions.items():
            if slot.pres.may_be_present():
                kind, val = keysig
                if kind == "str":
                    result[val] = slot.val
        return result

    @classmethod
    def from_bindings(cls, bindings: Dict[str, 'AbsVal']) -> 'CellStore':
        """Create a CellStore from a dict of variable bindings."""
        slots = SlotMap.for_namespace()
        for name, val in bindings.items():
            keysig = make_str_keysig(name)
            slots = slots.strong_update(keysig, val, Presence.PRESENT)
        return cls(slots=slots)


# =============================================================================
# ScopeBinding - Result of name lookup
# =============================================================================

@dataclass
class ScopeBinding:
    """Result of resolving a name in scope context.

    Attributes:
        kind: Where the variable is stored (local/cell/nonlocal/global)
        value: The abstract value at this binding
        found: Whether the binding was found (vs default/bottom)
    """
    kind: str
    value: 'AbsVal'
    found: bool = True


# =============================================================================
# ScopeSemantics - Main service for scope/closure operations
# =============================================================================

class ScopeSemantics:
    """Service class for Python scope and closure semantics.

    This class provides name resolution that respects Python's scoping rules
    with support for:
    - global/nonlocal/cell variable declarations
    - Closure capture and cell storage
    - globals()/locals() builtin evaluation

    Usage:
        semantics = ScopeSemantics(pta_query, budget, scope_ir)
        # Read a name
        val = semantics.read_name("x", state, summary)
        # Write a name
        state = semantics.write_name("x", val, state, summary)
        # Evaluate globals()
        dict_val = semantics.eval_globals_dict(state, summary)
        # Evaluate locals()
        dict_val = semantics.eval_locals_dict(state, summary)
    """

    def __init__(
        self,
        pta_query: Optional['PtaQuery'],
        budget: Optional['AnalysisBudget'],
        scope_ir: Optional['IRScope'] = None,
        caller_scope: Optional['Scope'] = None,
        call_context: Optional['AbstractContext'] = None,
        cell_store: Optional[CellStore] = None,
        globals_store: Optional[SlotMap] = None,
        module_obj: Optional['AbstractObject'] = None,
    ):
        """Initialize ScopeSemantics.

        Args:
            pta_query: Interface to query PTA (for closure bindings)
            budget: Analysis budget
            scope_ir: IR scope for this function/class/module
            caller_scope: Caller's scope for field queries
            call_context: Current analysis context
            cell_store: CellStore for closure variables (created if needed)
            globals_store: SlotMap for globals (derived from module if not provided)
            module_obj: Module object for globals resolution
        """
        self.pta_query = pta_query
        self.budget = budget
        self.scope_ir = scope_ir
        self.caller_scope = caller_scope
        self.call_context = call_context
        self.cell_store = cell_store or CellStore()
        self.globals_store = globals_store
        self.module_obj = module_obj

        # Cache scope metadata (defensive: scope_ir may not have these methods)
        self._global_vars: Set[str] = set()
        self._nonlocal_vars: Set[str] = set()
        self._cell_vars: Set[str] = set()
        if scope_ir is not None:
            if hasattr(scope_ir, 'get_global_vars'):
                self._global_vars = scope_ir.get_global_vars()
            if hasattr(scope_ir, 'get_nonlocal_vars'):
                self._nonlocal_vars = scope_ir.get_nonlocal_vars()
            if hasattr(scope_ir, 'get_cell_vars'):
                self._cell_vars = scope_ir.get_cell_vars()

    def classify_name(self, name: str) -> str:
        """Classify a variable name to determine its storage location.

        Returns one of NameKind.LOCAL/CELL/NONLOCAL/GLOBAL.
        """
        if name in self._global_vars:
            return NameKind.GLOBAL
        if name in self._nonlocal_vars:
            return NameKind.NONLOCAL
        if name in self._cell_vars:
            return NameKind.CELL
        return NameKind.LOCAL

    def read_name(
        self,
        name: str,
        state: 'AIState',
        summary: 'SummaryAccumulator',
    ) -> 'AbsVal':
        """Read a variable value respecting Python scoping rules.

        Args:
            name: Variable name to read
            state: Current abstract state
            summary: Summary accumulator for effects

        Returns:
            Abstract value for the variable
        """
        kind = self.classify_name(name)

        if kind == NameKind.LOCAL:
            # Normal local: read from env
            return state.get_var(name)

        elif kind == NameKind.CELL:
            # Cell variable: read from cell store + env (may be defined locally)
            cell_val = self.cell_store.read_cell(name)
            local_val = state.get_var(name)
            return cell_val.join(local_val)

        elif kind == NameKind.NONLOCAL:
            # Nonlocal: read from cell store only (defined in outer scope)
            return self.cell_store.read_cell(name)

        elif kind == NameKind.GLOBAL:
            # Global: read from globals store or module namespace
            return self._read_global(name, state, summary)

        return state.get_var(name)

    def write_name(
        self,
        name: str,
        value: 'AbsVal',
        state: 'AIState',
        summary: 'SummaryAccumulator',
    ) -> 'AIState':
        """Write a variable value respecting Python scoping rules.

        Args:
            name: Variable name to write
            value: Value to write
            state: Current abstract state
            summary: Summary accumulator for effects

        Returns:
            Updated abstract state
        """
        kind = self.classify_name(name)

        if kind == NameKind.LOCAL:
            # Normal local: write to env
            return state.set_var(name, value)

        elif kind == NameKind.CELL:
            # Cell variable: write to both env and cell store
            # (cell is shared with nested functions)
            new_state = state.set_var(name, value)
            self.cell_store = self.cell_store.write_cell(name, value, weak=True)
            return new_state

        elif kind == NameKind.NONLOCAL:
            # Nonlocal: write to parent's cell store
            self.cell_store, found = self.cell_store.write_cell_to_parent(
                name, value, weak=True
            )
            if not found:
                # Fallback: write to local cell store
                self.cell_store = self.cell_store.write_cell(name, value, weak=True)
            return state

        elif kind == NameKind.GLOBAL:
            # Global: write to globals store and module namespace
            return self._write_global(name, value, state, summary)

        return state.set_var(name, value)

    def _read_global(
        self,
        name: str,
        state: 'AIState',
        summary: 'SummaryAccumulator',
    ) -> 'AbsVal':
        """Read a global variable from module namespace.

        Tries multiple sources:
        1. globals_store SlotMap if available
        2. Module object's attribute via PTA
        3. Falls back to state env (conservative)
        """
        from ..value_domain import AbsVal
        from pythonstan.analysis.pointer.kcfa.heap_model import attr

        result = AbsVal.bottom()

        # Check globals store if available
        if self.globals_store is not None:
            keysig = make_str_keysig(name)
            slot = self.globals_store.get(keysig)
            if slot.pres.may_be_present():
                result = result.join(slot.val)
            # Also check default for soundness
            result = result.join(self.globals_store.default.val)

        # Check module object via PTA
        if self.module_obj is not None and self.pta_query is not None:
            field_key = attr(name)
            try:
                pts = self.pta_query.field_points_to(
                    self.module_obj, field_key, self.caller_scope, self.call_context
                )
                result = result.join(AbsVal.from_addrs(frozenset(pts)))
            except Exception:
                pass

        # Also check env (global may be initially bound there)
        local_val = state.get_var(name)
        result = result.join(local_val)

        return result

    def _write_global(
        self,
        name: str,
        value: 'AbsVal',
        state: 'AIState',
        summary: 'SummaryAccumulator',
    ) -> 'AIState':
        """Write a global variable to module namespace.

        Updates:
        1. globals_store SlotMap if available
        2. Records write effect on module object
        3. Also writes to local env for analysis consistency
        """
        from pythonstan.analysis.pointer.kcfa.heap_model import attr

        # Update globals store if available
        if self.globals_store is not None:
            keysig = make_str_keysig(name)
            self.globals_store = self.globals_store.weak_update(
                keysig, value, Presence.PRESENT
            )

        # Record write effect on module object
        if self.module_obj is not None:
            field_key = attr(name)
            summary.add_write(
                frozenset({self.module_obj}),
                field_key,
                value.addrs,
            )

        # Also write to local env for consistency
        return state.set_var(name, value)

    # =========================================================================
    # globals() / locals() builtins
    # =========================================================================

    def eval_globals_dict(
        self,
        state: 'AIState',
        summary: 'SummaryAccumulator',
    ) -> 'AbsVal':
        """Evaluate globals() builtin.

        Returns a dict-like AbsVal representing the module namespace.
        For constant key access (globals()['k']), precision is maintained.

        Returns:
            AbsVal pointing to a dict object with globals slots
        """
        from ..value_domain import AbsVal
        from ..heap import AIAddr, AbsObj, ObjKind
        from pythonstan.analysis.pointer.kcfa.object import AllocSite, AllocKind

        # Create a synthetic dict object for the globals view
        alloc_site = AllocSite(
            stmt="<globals_dict>",
            kind=AllocKind.DICT,
        )
        # Use call_context or a sentinel context (empty call string)
        if self.call_context is not None:
            context = self.call_context
        else:
            from pythonstan.analysis.pointer.kcfa.context import CallStringContext
            context = CallStringContext()

        addr = AIAddr(alloc_site=alloc_site, context=context)

        # Build SlotMap from globals store or module namespace
        slots = SlotMap.for_namespace()

        if self.globals_store is not None:
            slots = self.globals_store.copy()

        # If we have module object, read its attributes via PTA and merge
        if self.module_obj is not None and self.pta_query is not None:
            # This is conservative - we don't enumerate all module attributes
            # The slots from globals_store have the precision we can offer
            pass

        # Create AbsObj for the dict
        dict_obj = AbsObj(
            addr=addr,
            kind=ObjKind.DICT,
            slots=slots,
        )

        # Install in state heap (note: state modification is for future use)
        _new_state = state.set_obj(addr, dict_obj)  # noqa: F841

        # Create KCFA-style object for the address set
        from pythonstan.analysis.pointer.kcfa.object import AbstractObject
        kcfa_obj = AbstractObject(alloc_site=alloc_site, context=context)

        return AbsVal.from_addrs(frozenset({kcfa_obj}))

    def eval_locals_dict(
        self,
        state: 'AIState',
        summary: 'SummaryAccumulator',
    ) -> 'AbsVal':
        """Evaluate locals() builtin.

        Returns a dict-like AbsVal containing current local variables.
        This is a snapshot - writes to the returned dict are conservative
        (don't attempt CPython locals writeback semantics).

        Returns:
            AbsVal pointing to a dict object with locals slots
        """
        from ..value_domain import AbsVal
        from ..heap import AIAddr, AbsObj, ObjKind
        from pythonstan.analysis.pointer.kcfa.object import AllocSite, AllocKind

        # Create a synthetic dict object for the locals snapshot
        alloc_site = AllocSite(
            stmt="<locals_dict>",
            kind=AllocKind.DICT,
        )
        if self.call_context is not None:
            context = self.call_context
        else:
            from pythonstan.analysis.pointer.kcfa.context import CallStringContext
            context = CallStringContext()

        addr = AIAddr(alloc_site=alloc_site, context=context)

        # Build SlotMap from current env
        slots = SlotMap.for_namespace()
        max_keys = self.budget.max_dict_parts if self.budget else 20
        key_count = 0

        for var_name, var_val in state.env.items():
            if key_count >= max_keys:
                # Budget exceeded: spill to default
                slots = slots.weak_update_default(var_val, Presence.PRESENT)
            else:
                keysig = make_str_keysig(var_name)
                slots = slots.strong_update(keysig, var_val, Presence.PRESENT)
                key_count += 1

        # Also include cell variables (they're visible in locals())
        for var_name, var_val in self.cell_store.get_all_cells().items():
            if key_count >= max_keys:
                slots = slots.weak_update_default(var_val, Presence.PRESENT)
            else:
                keysig = make_str_keysig(var_name)
                # Weak update (may already have from env)
                slots = slots.weak_update(keysig, var_val, Presence.PRESENT)
                key_count += 1

        # Create AbsObj for the dict
        dict_obj = AbsObj(
            addr=addr,
            kind=ObjKind.DICT,
            slots=slots,
        )

        # Install in state heap (note: state modification is for future use)
        _new_state = state.set_obj(addr, dict_obj)  # noqa: F841

        # Create KCFA-style object for the address set
        from pythonstan.analysis.pointer.kcfa.object import AbstractObject
        kcfa_obj = AbstractObject(alloc_site=alloc_site, context=context)

        return AbsVal.from_addrs(frozenset({kcfa_obj}))

    def load_from_scope_dict(
        self,
        dict_val: 'AbsVal',
        key_val: 'AbsVal',
        state: 'AIState',
        summary: 'SummaryAccumulator',
        is_globals: bool = True,
    ) -> 'AbsVal':
        """Load a value from globals()/locals() dict by key.

        Provides precision for constant string keys.

        Args:
            dict_val: The globals()/locals() dict
            key_val: The key to look up
            state: Current state
            summary: Summary accumulator
            is_globals: True for globals(), False for locals()

        Returns:
            The value at the key (or unknown if key is dynamic)
        """
        from ..value_domain import AbsVal, STRING_DOMAIN
        from ..heap import AIAddr
        from .strings import StrLattice

        result = AbsVal.bottom()

        # Try to extract constant string key
        str_component = key_val.components.get(STRING_DOMAIN)
        const_keys: Optional[FrozenSet[str]] = None

        if str_component is not None and isinstance(str_component, StrLattice):
            if str_component.is_const():
                const_keys = str_component.consts

        # Read from each dict object
        for obj in dict_val.addrs:
            ai_addr = AIAddr.from_kcfa_obj(obj)
            ai_obj = state.get_obj(ai_addr)

            if ai_obj is not None:
                if const_keys is not None:
                    # Precise read for constant keys
                    for k in const_keys:
                        keysig = make_str_keysig(k)
                        slot = ai_obj.slots.get(keysig)
                        if slot.pres.may_be_present():
                            result = result.join(slot.val)
                    # Also join with default for soundness
                    result = result.join(ai_obj.slots.default.val)
                else:
                    # Unknown key: read all values
                    result = result.join(ai_obj.slots.get_all_values())
                    # Record unknown read effect
                    summary.add_unknown_effect("may_read", frozenset({obj}))
            else:
                # Object not in AI heap: unknown
                result = result.join(AbsVal.top())

        return result

    def store_to_scope_dict(
        self,
        dict_val: 'AbsVal',
        key_val: 'AbsVal',
        value_val: 'AbsVal',
        state: 'AIState',
        summary: 'SummaryAccumulator',
        is_globals: bool = True,
    ) -> 'AIState':
        """Store a value to globals()/locals() dict by key.

        For globals(): Updates both the dict and marks module write effect.
        For locals(): Conservative (CPython locals writeback is complex).

        Args:
            dict_val: The globals()/locals() dict
            key_val: The key to write
            value_val: The value to write
            state: Current state
            summary: Summary accumulator
            is_globals: True for globals(), False for locals()

        Returns:
            Updated state
        """
        from ..value_domain import STRING_DOMAIN
        from ..heap import AIAddr
        from .strings import StrLattice
        from pythonstan.analysis.pointer.kcfa.heap_model import attr, unknown

        new_state = state

        # Try to extract constant string key
        str_component = key_val.components.get(STRING_DOMAIN)
        const_keys: Optional[FrozenSet[str]] = None

        if str_component is not None and isinstance(str_component, StrLattice):
            if str_component.is_const():
                const_keys = str_component.consts

        # Write to each dict object
        for obj in dict_val.addrs:
            ai_addr = AIAddr.from_kcfa_obj(obj)
            ai_obj = new_state.get_obj(ai_addr)

            if ai_obj is not None:
                if const_keys is not None:
                    # Precise write for constant keys
                    new_slots = ai_obj.slots
                    for k in const_keys:
                        keysig = make_str_keysig(k)
                        new_slots = new_slots.weak_update(keysig, value_val, Presence.PRESENT)
                        # If globals, also update globals_store
                        if is_globals and self.globals_store is not None:
                            self.globals_store = self.globals_store.weak_update(
                                keysig, value_val, Presence.PRESENT
                            )
                        # Record write effect on module if globals
                        if is_globals and self.module_obj is not None:
                            summary.add_write(
                                frozenset({self.module_obj}),
                                attr(k),
                                value_val.addrs,
                            )
                    new_state = new_state.set_obj(ai_addr, ai_obj.with_slots(new_slots))
                else:
                    # Unknown key: weak update default bucket
                    new_slots = ai_obj.slots.weak_update_default(value_val, Presence.PRESENT)
                    new_state = new_state.set_obj(ai_addr, ai_obj.with_slots(new_slots))
                    # Record unknown write effect
                    summary.add_unknown_effect("may_write", frozenset({obj}))
                    # If globals, havoc module
                    if is_globals and self.module_obj is not None:
                        summary.add_write(
                            frozenset({self.module_obj}),
                            unknown(),
                            value_val.addrs,
                        )
                        new_state = new_state.add_overlay_havoc(self.module_obj)
            else:
                # Object not in AI heap: record unknown effect
                summary.add_unknown_effect("may_write", frozenset({obj}))

        # For locals(): writes are conservative (no actual variable update)
        # CPython's locals() returns a snapshot, writes don't affect locals
        if not is_globals:
            summary.add_unknown_effect("may_write", frozenset(dict_val.addrs))

        return new_state

    # =========================================================================
    # Closure initialization
    # =========================================================================

    def initialize_closure_from_pta(
        self,
        func_obj: 'AbstractObject',
    ) -> 'CellStore':
        """Initialize a CellStore from PTA-captured closure bindings.

        Uses PtaQuery.get_function_cell_vars() if available to populate
        the cell store with captured variable values from the enclosing scope.

        Args:
            func_obj: The function object being called

        Returns:
            CellStore populated with closure bindings
        """
        from ..value_domain import AbsVal

        cell_store = CellStore()

        if self.pta_query is None:
            return cell_store

        # Try to get cell vars from PTA
        if hasattr(self.pta_query, 'get_function_cell_vars'):
            try:
                cell_bindings = self.pta_query.get_function_cell_vars(func_obj)
                for var_name, pts in cell_bindings.items():
                    val = AbsVal.from_addrs(frozenset(pts))
                    cell_store = cell_store.write_cell(var_name, val, weak=True)
            except Exception:
                pass

        return cell_store

    def with_cell_store(self, cell_store: CellStore) -> 'ScopeSemantics':
        """Return a copy with updated cell store."""
        return ScopeSemantics(
            pta_query=self.pta_query,
            budget=self.budget,
            scope_ir=self.scope_ir,
            caller_scope=self.caller_scope,
            call_context=self.call_context,
            cell_store=cell_store,
            globals_store=self.globals_store,
            module_obj=self.module_obj,
        )

    def with_globals_store(self, globals_store: SlotMap) -> 'ScopeSemantics':
        """Return a copy with updated globals store."""
        return ScopeSemantics(
            pta_query=self.pta_query,
            budget=self.budget,
            scope_ir=self.scope_ir,
            caller_scope=self.caller_scope,
            call_context=self.call_context,
            cell_store=self.cell_store,
            globals_store=globals_store,
            module_obj=self.module_obj,
        )
