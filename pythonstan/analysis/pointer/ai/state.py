"""Abstract state for AI intraprocedural analysis.

This module defines AIState which tracks:
- env: Variable -> AbsVal (local environment)
- heap: AIAddr -> AbsObj (heap objects)
- overlay: Intra-analysis field writes (for read-after-write)
- predicates: TAC temporary -> predicate info (for value partition refinement)
- exn: Exception state (optional)
"""

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    FrozenSet,
    Iterator,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

from .heap import AIAddr, AbsObj, SlotMap, Slot, Presence, ContainerSlots, ObjKind, AddrSet, FieldStore
from .domains.mapslots import field_to_keysig, keysig_to_field, make_str_keysig


# =============================================================================
# PredicateInfo - Info about TAC temporaries holding predicate results
# =============================================================================

@dataclass(frozen=True)
class PredicateInfo:
    """Information about a predicate result stored in a TAC temporary.
    
    This is used to track `k in d` expressions that have been assigned to
    temporaries so that ConditionRefiner can refine dict membership.
    
    Attributes:
        kind: Predicate kind ("dict_contains", "dict_not_contains")
        dict_var: Variable name holding the dict
        key_var: Variable name holding the key (or None for literal keys)
        key_literal: Literal key value (str/int) if key_var is None
        negated: Whether the predicate is negated
    """
    kind: str
    dict_var: str
    key_var: Optional[str] = None
    key_literal: Optional[Any] = None
    negated: bool = False
from .value_domain import (
    AbsVal,
    DomainRegistry,
    get_default_registry,
    STRING_DOMAIN,
    BOOL_DOMAIN,
    NONE_DOMAIN,
    INT_DOMAIN,
)

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.heap_model import Field
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from .api import PtaQuery, ArgBinding, AnalysisBudget

__all__ = ["AIState", "AbsVal", "PredicateInfo"]


# =============================================================================
# AIState - Abstract state for intraprocedural analysis
# =============================================================================

@dataclass
class AIState:
    """Abstract state for AI intraprocedural analysis.
    
    Attributes:
        env: Variable name -> AbsVal mapping
        heap: AIAddr -> AbsObj mapping (AI-local heap view)
        overlay: Intra-analysis field writes (overlay on PTA heap)
        predicates: TAC temporary -> PredicateInfo (for value partition refinement)
        exn: Exception state (optional: None = normal, else may raise)
        pta_query: Reference to PTA for querying points-to
        budget: Analysis budget for widening caps
    """
    env: Dict[str, AbsVal] = field(default_factory=dict)
    heap: Dict[AIAddr, AbsObj] = field(default_factory=dict)
    overlay: Dict[Tuple['AbstractObject', 'Field'], AbsVal] = field(default_factory=dict)
    overlay_havoc: Set['AbstractObject'] = field(default_factory=set)
    predicates: Dict[str, PredicateInfo] = field(default_factory=dict)
    exn: Optional[AbsVal] = None
    pta_query: Optional['PtaQuery'] = None
    budget: Optional['AnalysisBudget'] = None
    registry: DomainRegistry = field(default_factory=get_default_registry)

    def copy(self) -> 'AIState':
        """Create a deep-ish copy (copies env/heap dicts)."""
        return AIState(
            env=dict(self.env),
            heap={k: v.copy() for k, v in self.heap.items()},
            overlay=dict(self.overlay),
            overlay_havoc=set(self.overlay_havoc),
            predicates=dict(self.predicates),
            exn=self.exn,
            pta_query=self.pta_query,
            budget=self.budget,
            registry=self.registry,
        )

    @classmethod
    def bottom(cls, pta_query: Optional['PtaQuery'] = None, budget: Optional['AnalysisBudget'] = None) -> 'AIState':
        """Create bottom state (unreachable)."""
        return cls(pta_query=pta_query, budget=budget, registry=get_default_registry())

    @classmethod
    def from_arg_bindings(
        cls,
        arg_bindings: List['ArgBinding'],
        pta_query: Optional['PtaQuery'] = None,
        budget: Optional['AnalysisBudget'] = None,
    ) -> 'AIState':
        """Create initial state from argument bindings.
        
        Args:
            arg_bindings: Parameter -> PointsToSet bindings from caller
            pta_query: PTA query interface
            budget: Analysis budget
            
        Returns:
            Initial AIState with parameters bound
        """
        env: Dict[str, AbsVal] = {}
        registry = get_default_registry()
        for binding in arg_bindings:
            # Prefer full AbsVal if provided (includes domain components)
            if binding.value is not None:
                reduced = registry.reduce_value(
                    binding.param_name,
                    binding.value.addrs,
                    dict(binding.value.components),
                    budget=budget,
                )
                env[binding.param_name] = binding.value.with_components(reduced)
            else:
                val = AbsVal.from_addrs(binding.points_to)
                reduced = registry.reduce_value(
                    binding.param_name,
                    val.addrs,
                    dict(val.components),
                    budget=budget,
                )
                env[binding.param_name] = val.with_components(reduced)
        
        return cls(env=env, pta_query=pta_query, budget=budget, registry=get_default_registry())

    def _reduce_value(self, var: str, val: AbsVal) -> AbsVal:
        """Run reduction across domains for a variable value."""
        if self.registry is None:
            return val
        components = self.registry.reduce_value(
            var,
            val.addrs,
            dict(val.components),
            budget=self.budget,
        )
        return val.with_components(components)

    # -------------------------------------------------------------------------
    # Environment operations
    # -------------------------------------------------------------------------

    def get_var(self, name: str) -> AbsVal:
        """Get abstract value for variable."""
        return self.env.get(name, AbsVal.bottom())

    def set_var(self, name: str, val: AbsVal) -> 'AIState':
        """Set variable value (returns new state)."""
        new_state = self.copy()
        new_state.env[name] = new_state._reduce_value(name, val)
        return new_state

    def weak_set_var(self, name: str, val: AbsVal) -> 'AIState':
        """Weak update variable (join with existing)."""
        new_state = self.copy()
        if name in new_state.env:
            joined = new_state.env[name].join(val)
            new_state.env[name] = new_state._reduce_value(name, joined)
        else:
            new_state.env[name] = new_state._reduce_value(name, val)
        return new_state

    # -------------------------------------------------------------------------
    # Overlay heap operations (intra-analysis view)
    # -------------------------------------------------------------------------

    def add_overlay_write(
        self,
        obj: 'AbstractObject',
        field_key: 'Field',
        value: AbsVal,
    ) -> 'AIState':
        """Weakly update overlay for a field write."""
        if value.is_bottom():
            return self
        new_state = self.copy()
        key = (obj, field_key)
        existing = new_state.overlay.get(key, AbsVal.bottom())
        new_state.overlay[key] = existing.join(value)
        return new_state

    def add_overlay_havoc(self, obj: 'AbstractObject') -> 'AIState':
        """Mark an object as having unknown writes within this analysis."""
        new_state = self.copy()
        new_state.overlay_havoc.add(obj)
        return new_state

    def read_field_value(
        self,
        obj: 'AbstractObject',
        field_key: 'Field',
        caller_scope: Optional['Scope'],
        call_context: Optional['AbstractContext'],
    ) -> Tuple[AbsVal, bool]:
        """Read field value with overlay + AI heap + PTA fallback."""
        overlay_val = self.overlay.get((obj, field_key), AbsVal.bottom())
        havoc = obj in self.overlay_havoc

        heap_val = AbsVal.bottom()
        ai_addr = AIAddr.from_kcfa_obj(obj)
        ai_obj = self.heap.get(ai_addr)
        if ai_obj is not None:
            # Convert Field to KeySig and read from unified SlotMap
            keysig = field_to_keysig(field_key)
            if keysig is not None:
                slot = ai_obj.slots.get(keysig)
                heap_val = slot.val
                # Also join with default bucket for soundness
                heap_val = heap_val.join(ai_obj.slots.default.val)
            else:
                # Unknown field - read from default
                heap_val = ai_obj.slots.get_all_values()

        pta_val = AbsVal.bottom()
        if self.pta_query is not None:
            try:
                pta_pts = self.pta_query.field_points_to(obj, field_key, caller_scope, call_context)
                pta_val = AbsVal.from_addrs(frozenset(pta_pts))
            except Exception:
                pta_val = AbsVal.bottom()

        merged = overlay_val.join(heap_val).join(pta_val)
        return merged, havoc

    def read_field_points_to(
        self,
        obj: 'AbstractObject',
        field_key: 'Field',
        caller_scope: Optional['Scope'],
        call_context: Optional['AbstractContext'],
    ) -> Tuple[FrozenSet['AbstractObject'], bool]:
        """Read field points-to with overlay, returning (pts, is_havoc)."""
        val, havoc = self.read_field_value(obj, field_key, caller_scope, call_context)
        return val.addrs, havoc

    def write_field_value(
        self,
        obj: 'AbstractObject',
        field_key: 'Field',
        value: AbsVal,
        update_heap: bool = False,
    ) -> 'AIState':
        """Weakly update overlay (and optional AI heap) for a field write."""
        new_state = self.add_overlay_write(obj, field_key, value)
        if update_heap:
            ai_addr = AIAddr.from_kcfa_obj(obj)
            ai_obj = new_state.heap.get(ai_addr)
            if ai_obj is not None:
                # Convert Field to KeySig and write to unified SlotMap
                keysig = field_to_keysig(field_key)
                if keysig is not None:
                    new_slots = ai_obj.slots.weak_update(keysig, value, Presence.PRESENT)
                    new_state = new_state.set_obj(ai_addr, ai_obj.with_slots(new_slots))
                else:
                    # Unknown field - update default bucket
                    new_slots = ai_obj.slots.weak_update_default(value, Presence.PRESENT)
                    new_state = new_state.set_obj(ai_addr, ai_obj.with_slots(new_slots))
        return new_state

    # -------------------------------------------------------------------------
    # Heap operations (AI-local view)
    # -------------------------------------------------------------------------

    def get_obj(self, addr: AIAddr) -> Optional[AbsObj]:
        """Get heap object at address."""
        return self.heap.get(addr)

    def set_obj(self, addr: AIAddr, obj: AbsObj) -> 'AIState':
        """Set heap object (returns new state)."""
        new_state = self.copy()
        new_state.heap[addr] = obj
        return new_state

    def update_obj(self, addr: AIAddr, obj: AbsObj) -> 'AIState':
        """Update heap object by joining with existing."""
        new_state = self.copy()
        if addr in new_state.heap:
            new_state.heap[addr] = new_state.heap[addr].join(obj)
        else:
            new_state.heap[addr] = obj
        return new_state

    # -------------------------------------------------------------------------
    # Lattice operations
    # -------------------------------------------------------------------------

    def is_bottom(self) -> bool:
        """Check if state is bottom (unreachable)."""
        return len(self.env) == 0 and len(self.heap) == 0 and len(self.overlay) == 0

    def join(self, other: 'AIState') -> 'AIState':
        """Join two states (⊔)."""
        # Join environments
        new_env: Dict[str, AbsVal] = {}
        all_vars = set(self.env.keys()) | set(other.env.keys())
        for v in all_vars:
            v1 = self.env.get(v, AbsVal.bottom())
            v2 = other.env.get(v, AbsVal.bottom())
            joined = v1.join(v2)
            new_env[v] = self._reduce_value(v, joined)

        # Join heaps
        new_heap: Dict[AIAddr, AbsObj] = {}
        all_addrs = set(self.heap.keys()) | set(other.heap.keys())
        for a in all_addrs:
            if a in self.heap and a in other.heap:
                new_heap[a] = self.heap[a].join(other.heap[a])
            elif a in self.heap:
                new_heap[a] = self.heap[a]
            else:
                new_heap[a] = other.heap[a]

        # Join overlays
        new_overlay: Dict[Tuple['AbstractObject', 'Field'], AbsVal] = {}
        all_overlay_keys = set(self.overlay.keys()) | set(other.overlay.keys())
        for k in all_overlay_keys:
            v1 = self.overlay.get(k, AbsVal.bottom())
            v2 = other.overlay.get(k, AbsVal.bottom())
            new_overlay[k] = v1.join(v2)

        new_overlay_havoc = self.overlay_havoc | other.overlay_havoc

        # Join predicates (keep common predicates)
        new_predicates: Dict[str, PredicateInfo] = {}
        for k in self.predicates:
            if k in other.predicates and self.predicates[k] == other.predicates[k]:
                new_predicates[k] = self.predicates[k]

        # Join exceptions
        new_exn = None
        if self.exn is not None and other.exn is not None:
            new_exn = self.exn.join(other.exn)
        elif self.exn is not None:
            new_exn = self.exn
        elif other.exn is not None:
            new_exn = other.exn

        return AIState(
            env=new_env,
            heap=new_heap,
            overlay=new_overlay,
            overlay_havoc=new_overlay_havoc,
            predicates=new_predicates,
            exn=new_exn,
            pta_query=self.pta_query,
            budget=self.budget,
            registry=self.registry,
        )

    def leq(self, other: 'AIState') -> bool:
        """Check if self ⊑ other."""
        # Check all vars in self
        for v, val in self.env.items():
            other_val = other.env.get(v, AbsVal.bottom())
            if not val.leq(other_val):
                return False
        
        # Check heap objects
        for a, obj in self.heap.items():
            if a not in other.heap:
                return False
            # Use AbsObj.leq which includes slots (unified SlotMap) and container_slots
            if not obj.leq(other.heap[a]):
                return False

        # Check overlays
        for k, v in self.overlay.items():
            other_v = other.overlay.get(k, AbsVal.bottom())
            if not v.leq(other_v):
                return False
        if not self.overlay_havoc <= other.overlay_havoc:
            return False
        
        # Check predicates (self's predicates should be ⊑ other's)
        for k, pred in self.predicates.items():
            if k not in other.predicates:
                # Having extra predicates in self is fine (more refined)
                pass
            elif self.predicates[k] != other.predicates[k]:
                # Different predicates - conservative, treat as not leq
                return False
        
        return True

    def widen(self, other: 'AIState') -> 'AIState':
        """Widen state (∇).
        
        Apply widening to address sets, string sets, and field stores.
        From ai_spec.md §10.1: address-set widening caps growth via summarization.
        """
        max_addrs = self.budget.max_addr_set_size if self.budget else 50

        # Widen environments
        new_env: Dict[str, AbsVal] = {}
        all_vars = set(self.env.keys()) | set(other.env.keys())
        for v in all_vars:
            v1 = self.env.get(v, AbsVal.bottom())
            v2 = other.env.get(v, AbsVal.bottom())
            widened = v1.widen(v2, max_addrs=max_addrs)
            new_env[v] = self._reduce_value(v, widened)

        # Widen heaps
        new_heap: Dict[AIAddr, AbsObj] = {}
        all_addrs = set(self.heap.keys()) | set(other.heap.keys())
        for a in all_addrs:
            if a in self.heap and a in other.heap:
                new_heap[a] = self.heap[a].widen(other.heap[a])
            elif a in self.heap:
                new_heap[a] = self.heap[a]
            else:
                new_heap[a] = other.heap[a]

        # Widen overlays (use join semantics)
        new_overlay: Dict[Tuple['AbstractObject', 'Field'], AbsVal] = {}
        all_overlay_keys = set(self.overlay.keys()) | set(other.overlay.keys())
        for k in all_overlay_keys:
            v1 = self.overlay.get(k, AbsVal.bottom())
            v2 = other.overlay.get(k, AbsVal.bottom())
            new_overlay[k] = v1.join(v2)
        new_overlay_havoc = self.overlay_havoc | other.overlay_havoc

        # Widen predicates (keep common predicates)
        new_predicates: Dict[str, PredicateInfo] = {}
        for k in self.predicates:
            if k in other.predicates and self.predicates[k] == other.predicates[k]:
                new_predicates[k] = self.predicates[k]

        return AIState(
            env=new_env,
            heap=new_heap,
            overlay=new_overlay,
            overlay_havoc=new_overlay_havoc,
            predicates=new_predicates,
            exn=self.exn.join(other.exn) if self.exn and other.exn else self.exn or other.exn,
            pta_query=self.pta_query,
            budget=self.budget,
            registry=self.registry,
        )

    def narrow(self, other: 'AIState') -> 'AIState':
        """Narrow state (△).
        
        Apply meet to refine after widening. Narrows both environment and heap.
        From ai_spec.md §10.6: narrowing uses meet with branch constraints.
        """
        # Narrow environments
        new_env: Dict[str, AbsVal] = {}
        for v in self.env:
            if v in other.env:
                narrowed = self.env[v].meet(other.env[v])
                new_env[v] = self._reduce_value(v, narrowed)
            else:
                new_env[v] = self.env[v]

        # Narrow heaps: apply meet to matching objects
        new_heap: Dict[AIAddr, AbsObj] = {}
        for a in self.heap:
            if a in other.heap:
                # Narrow the heap object
                new_heap[a] = self.heap[a].narrow(other.heap[a])
            else:
                new_heap[a] = self.heap[a]

        # Narrow overlays (intersection where possible)
        new_overlay: Dict[Tuple['AbstractObject', 'Field'], AbsVal] = {}
        for k, v in self.overlay.items():
            if k in other.overlay:
                new_overlay[k] = v.meet(other.overlay[k])
            else:
                new_overlay[k] = v
        new_overlay_havoc = self.overlay_havoc & other.overlay_havoc

        # Narrow predicates (union - keep all predicates from both)
        new_predicates: Dict[str, PredicateInfo] = dict(self.predicates)
        new_predicates.update(other.predicates)

        return AIState(
            env=new_env,
            heap=new_heap,
            overlay=new_overlay,
            overlay_havoc=new_overlay_havoc,
            predicates=new_predicates,
            exn=self.exn,
            pta_query=self.pta_query,
            budget=self.budget,
            registry=self.registry,
        )

    # -------------------------------------------------------------------------
    # Predicate operations (for value partition refinement)
    # -------------------------------------------------------------------------

    def add_predicate(self, var_name: str, info: PredicateInfo) -> 'AIState':
        """Add a predicate for a TAC temporary variable."""
        new_state = self.copy()
        new_state.predicates[var_name] = info
        return new_state

    def get_predicate(self, var_name: str) -> Optional[PredicateInfo]:
        """Get predicate info for a TAC temporary variable."""
        return self.predicates.get(var_name)

    def __str__(self) -> str:
        env_str = ", ".join(f"{k}={v}" for k, v in sorted(self.env.items()))
        return f"AIState(env={{{env_str}}}, heap={len(self.heap)} objs, overlay={len(self.overlay)}, preds={len(self.predicates)})"

