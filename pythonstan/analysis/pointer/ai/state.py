"""Abstract state for AI intraprocedural analysis.

This module defines AIState which tracks:
- env: Variable -> AbsVal (local environment)
- heap: AIAddr -> AbsObj (heap objects)
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

from .heap import AIAddr, AbsObj, FieldStore, ContainerSlots, ObjKind, AddrSet

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.heap_model import Field
    from .api import PtaQuery, ArgBinding, AnalysisBudget

__all__ = ["AIState", "AbsVal"]


# =============================================================================
# AbsVal - Abstract values
# =============================================================================

@dataclass(frozen=True)
class AbsVal:
    """Abstract value = ⟨AddrSet, PrimComponent⟩.
    
    From ai_spec.md §3.3:
    - A ⊆ Addr: possible heap addresses
    - P ∈ Prim: primitive component
    
    For v1 we focus on address sets; primitive tracking is via plugins.
    
    Attributes:
        addrs: Set of abstract objects this value may point to
        str_const: Optional constant string value(s) for reflective keys
            - None = unknown string (top for string component)
            - empty frozenset = definitely not a string (bottom for string component)
            - non-empty frozenset = known constant strings
        may_be_none: Whether value may be None
        bool_val: Optional boolean value (True/False/None=unknown)
    """
    addrs: FrozenSet['AbstractObject'] = field(default_factory=frozenset)
    str_const: Optional[FrozenSet[str]] = None  # None = unknown, empty = definitely not string
    may_be_none: bool = False
    bool_val: Optional[bool] = None  # None = unknown

    @classmethod
    def bottom(cls) -> 'AbsVal':
        """Create bottom value (unreachable / no information).
        
        NOTE: Bottom is NOT the same as "unknown" - it means "no possible value".
        For a variable that doesn't exist yet, we use bottom.
        Joining bottom with any value V yields V.
        """
        # str_const=frozenset() means "definitely not a string" (empty set of possible strings)
        # This is different from str_const=None which means "unknown string" (top)
        return cls(addrs=frozenset(), str_const=frozenset(), may_be_none=False, bool_val=None)

    @classmethod
    def top(cls) -> 'AbsVal':
        """Create top value (unknown / any possible value)."""
        return cls(str_const=None, may_be_none=True, bool_val=None)

    @classmethod
    def from_addrs(cls, addrs: FrozenSet['AbstractObject']) -> 'AbsVal':
        """Create value from address set."""
        return cls(addrs=addrs)

    @classmethod
    def from_str(cls, s: str) -> 'AbsVal':
        """Create value from constant string."""
        return cls(str_const=frozenset({s}))

    @classmethod
    def from_strs(cls, strs: FrozenSet[str]) -> 'AbsVal':
        """Create value from set of constant strings."""
        return cls(str_const=strs)

    @classmethod
    def none_val(cls) -> 'AbsVal':
        """Create None value."""
        return cls(may_be_none=True)

    @classmethod
    def bool_true(cls) -> 'AbsVal':
        """Create True value."""
        return cls(bool_val=True)

    @classmethod
    def bool_false(cls) -> 'AbsVal':
        """Create False value."""
        return cls(bool_val=False)

    def is_bottom(self) -> bool:
        """Check if this is bottom (no possible value)."""
        return (
            len(self.addrs) == 0 and
            self.str_const is not None and len(self.str_const) == 0 and
            not self.may_be_none and
            self.bool_val is None
        )

    def is_const_str(self) -> bool:
        """Check if this is a constant string (finite non-empty set)."""
        return self.str_const is not None and len(self.str_const) > 0

    def is_unknown_str(self) -> bool:
        """Check if string component is unknown (top)."""
        return self.str_const is None

    def is_str_bottom(self) -> bool:
        """Check if string component is bottom (empty set = definitely not a string)."""
        return self.str_const is not None and len(self.str_const) == 0

    def get_const_strs(self) -> Optional[FrozenSet[str]]:
        """Get constant strings if enumerable, else None."""
        return self.str_const

    def join(self, other: 'AbsVal') -> 'AbsVal':
        """Join two abstract values (⊔).
        
        Bottom is the identity for join: bottom ⊔ V = V.
        """
        # Handle bottom cases: bottom is identity for join
        if self.is_bottom():
            return other
        if other.is_bottom():
            return self
        
        # Join addresses
        new_addrs = self.addrs | other.addrs

        # Join string constants
        # None (unknown/top) dominates; empty set (bottom) is identity
        if self.str_const is None or other.str_const is None:
            new_str = None
        elif len(self.str_const) == 0:
            new_str = other.str_const
        elif len(other.str_const) == 0:
            new_str = self.str_const
        else:
            new_str = self.str_const | other.str_const

        # Join bool
        # None (unknown) dominates specific values
        if self.bool_val is None or other.bool_val is None:
            new_bool = None
        elif self.bool_val == other.bool_val:
            new_bool = self.bool_val
        else:
            new_bool = None  # True ⊔ False = unknown

        return AbsVal(
            addrs=new_addrs,
            str_const=new_str,
            may_be_none=self.may_be_none or other.may_be_none,
            bool_val=new_bool,
        )

    def meet(self, other: 'AbsVal') -> 'AbsVal':
        """Meet two abstract values (⊓)."""
        # Meet addresses
        new_addrs = self.addrs & other.addrs

        # Meet string constants
        if self.str_const is None:
            new_str = other.str_const
        elif other.str_const is None:
            new_str = self.str_const
        else:
            new_str = self.str_const & other.str_const

        # Meet bool
        if self.bool_val is None:
            new_bool = other.bool_val
        elif other.bool_val is None:
            new_bool = self.bool_val
        elif self.bool_val == other.bool_val:
            new_bool = self.bool_val
        else:
            new_bool = None  # Contradiction -> could be bottom

        return AbsVal(
            addrs=new_addrs,
            str_const=new_str,
            may_be_none=self.may_be_none and other.may_be_none,
            bool_val=new_bool,
        )

    def leq(self, other: 'AbsVal') -> bool:
        """Check if self ⊑ other.
        
        Lattice ordering where None = top (unknown), frozenset() = bottom.
        """
        if not self.addrs <= other.addrs:
            return False
        
        # String lattice: None (unknown) is top, empty set is bottom
        # specific ⊑ unknown (top), but unknown ⊏ specific
        if self.str_const is None:
            # self is unknown (top) - only ≤ other if other is also unknown
            if other.str_const is not None:
                return False  # unknown ⊏ specific
        elif other.str_const is not None:
            # Both are specific sets - check subset
            if not self.str_const <= other.str_const:
                return False
        # else: self is specific, other is unknown → self ⊑ other ✓
        
        if self.may_be_none and not other.may_be_none:
            return False
        
        # Bool lattice: None (unknown) is top
        if self.bool_val is None:
            # self is unknown - only ≤ other if other is also unknown
            if other.bool_val is not None:
                return False
        elif other.bool_val is not None:
            # Both are specific - must be equal
            if self.bool_val != other.bool_val:
                return False
        # else: self is specific, other is unknown → self ⊑ other ✓
        
        return True

    def widen(self, other: 'AbsVal', max_strs: int = 10, max_addrs: int = 50) -> 'AbsVal':
        """Widen abstract value (∇).
        
        From ai_spec.md §10.1:
        - String widening: if too many constants, go to unknown.
        - Address widening: if too many addresses, summarize by alloc-site.
        
        Args:
            other: Other value to widen with
            max_strs: Maximum string constants before widening to unknown
            max_addrs: Maximum addresses before summarization
            
        Returns:
            Widened abstract value
        """
        joined = self.join(other)
        
        new_addrs = joined.addrs
        new_str = joined.str_const
        
        # String widening: too many constants -> unknown
        if new_str is not None and len(new_str) > max_strs:
            new_str = None  # Widen to unknown
        
        # Address set widening: if too large, summarize by alloc-site
        # From ai_spec.md §10.1: A ∇_A A' = {summary(site, ctx) | ...} when |A ∪ A'| > cap
        if len(new_addrs) > max_addrs:
            new_addrs = self._summarize_addrs_by_alloc_site(new_addrs)
        
        return AbsVal(
            addrs=new_addrs,
            str_const=new_str,
            may_be_none=joined.may_be_none,
            bool_val=joined.bool_val,
        )

    @staticmethod
    def _summarize_addrs_by_alloc_site(
        addrs: FrozenSet['AbstractObject']
    ) -> FrozenSet['AbstractObject']:
        """Summarize addresses by collapsing contexts at the same alloc-site.
        
        From ai_spec.md §10.1:
        summary(site, ctx) = (site, trunc_{k'}(ctx)) where k' ≤ k
        
        For each alloc-site with multiple objects, keep only one representative
        (the one with the shortest/most summarized context).
        
        Args:
            addrs: Set of abstract objects to summarize
            
        Returns:
            Summarized address set (smaller or same size)
        """
        from collections import defaultdict
        
        # Group objects by alloc_site
        by_site: Dict[Any, List['AbstractObject']] = defaultdict(list)
        for obj in addrs:
            by_site[obj.alloc_site].append(obj)
        
        # For each site, pick the object with shortest context or create summary
        result: Set['AbstractObject'] = set()
        for site, objs in by_site.items():
            if len(objs) == 1:
                # Only one object at this site, keep it
                result.add(objs[0])
            else:
                # Multiple objects at same site - pick most summarized context
                # For now, pick the one with the shortest context representation
                # This is a heuristic; a full implementation would create a true
                # summary context by truncating to k'=0
                best = min(objs, key=lambda o: len(str(o.context)))
                result.add(best)
                
                # Alternatively, we could try to create a summary context:
                # For CallStringContext, we could use k=0 (empty context)
                # For ObjectContext, we could use depth=0
                # But this requires constructing new AbstractObjects which may
                # not be in the kcfa's object pool. For now, use the heuristic.
        
        return frozenset(result)

    def with_addrs(self, addrs: FrozenSet['AbstractObject']) -> 'AbsVal':
        """Return copy with new address set."""
        return AbsVal(
            addrs=addrs,
            str_const=self.str_const,
            may_be_none=self.may_be_none,
            bool_val=self.bool_val,
        )

    def __str__(self) -> str:
        parts = []
        if self.addrs:
            parts.append(f"addrs={{{len(self.addrs)} objs}})")
        if self.str_const is not None:
            if len(self.str_const) <= 3:
                parts.append(f"str={self.str_const}")
            else:
                parts.append(f"str={{{len(self.str_const)} strs}}")
        elif self.str_const is None:
            parts.append("str=⊤")
        if self.may_be_none:
            parts.append("None")
        if self.bool_val is not None:
            parts.append(f"bool={self.bool_val}")
        return f"AbsVal({', '.join(parts)})"


# =============================================================================
# AIState - Abstract state for intraprocedural analysis
# =============================================================================

@dataclass
class AIState:
    """Abstract state for AI intraprocedural analysis.
    
    Attributes:
        env: Variable name -> AbsVal mapping
        heap: AIAddr -> AbsObj mapping (AI-local heap view)
        exn: Exception state (optional: None = normal, else may raise)
        pta_query: Reference to PTA for querying points-to
        budget: Analysis budget for widening caps
    """
    env: Dict[str, AbsVal] = field(default_factory=dict)
    heap: Dict[AIAddr, AbsObj] = field(default_factory=dict)
    exn: Optional[AbsVal] = None
    pta_query: Optional['PtaQuery'] = None
    budget: Optional['AnalysisBudget'] = None

    def copy(self) -> 'AIState':
        """Create a deep-ish copy (copies env/heap dicts)."""
        return AIState(
            env=dict(self.env),
            heap={k: v.copy() for k, v in self.heap.items()},
            exn=self.exn,
            pta_query=self.pta_query,
            budget=self.budget,
        )

    @classmethod
    def bottom(cls, pta_query: Optional['PtaQuery'] = None, budget: Optional['AnalysisBudget'] = None) -> 'AIState':
        """Create bottom state (unreachable)."""
        return cls(pta_query=pta_query, budget=budget)

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
        for binding in arg_bindings:
            # Prefer full AbsVal if provided (includes domain facts like str_const)
            if binding.value is not None:
                env[binding.param_name] = binding.value
            else:
                env[binding.param_name] = AbsVal.from_addrs(binding.points_to)
        
        return cls(env=env, pta_query=pta_query, budget=budget)

    # -------------------------------------------------------------------------
    # Environment operations
    # -------------------------------------------------------------------------

    def get_var(self, name: str) -> AbsVal:
        """Get abstract value for variable."""
        return self.env.get(name, AbsVal.bottom())

    def set_var(self, name: str, val: AbsVal) -> 'AIState':
        """Set variable value (returns new state)."""
        new_state = self.copy()
        new_state.env[name] = val
        return new_state

    def weak_set_var(self, name: str, val: AbsVal) -> 'AIState':
        """Weak update variable (join with existing)."""
        new_state = self.copy()
        if name in new_state.env:
            new_state.env[name] = new_state.env[name].join(val)
        else:
            new_state.env[name] = val
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
        return len(self.env) == 0 and len(self.heap) == 0

    def join(self, other: 'AIState') -> 'AIState':
        """Join two states (⊔)."""
        # Join environments
        new_env: Dict[str, AbsVal] = {}
        all_vars = set(self.env.keys()) | set(other.env.keys())
        for v in all_vars:
            v1 = self.env.get(v, AbsVal.bottom())
            v2 = other.env.get(v, AbsVal.bottom())
            new_env[v] = v1.join(v2)

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
            exn=new_exn,
            pta_query=self.pta_query,
            budget=self.budget,
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
            # Simplified: just check dict stores
            if not obj.dict_store.leq(other.heap[a].dict_store):
                return False
        
        return True

    def widen(self, other: 'AIState') -> 'AIState':
        """Widen state (∇).
        
        Apply widening to address sets, string sets, and field stores.
        From ai_spec.md §10.1: address-set widening caps growth via summarization.
        """
        max_strs = self.budget.max_const_strings if self.budget else 10
        max_addrs = self.budget.max_addr_set_size if self.budget else 50

        # Widen environments
        new_env: Dict[str, AbsVal] = {}
        all_vars = set(self.env.keys()) | set(other.env.keys())
        for v in all_vars:
            v1 = self.env.get(v, AbsVal.bottom())
            v2 = other.env.get(v, AbsVal.bottom())
            widened = v1.widen(v2, max_strs=max_strs, max_addrs=max_addrs)
            new_env[v] = widened

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

        return AIState(
            env=new_env,
            heap=new_heap,
            exn=self.exn.join(other.exn) if self.exn and other.exn else self.exn or other.exn,
            pta_query=self.pta_query,
            budget=self.budget,
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
                new_env[v] = self.env[v].meet(other.env[v])
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

        return AIState(
            env=new_env,
            heap=new_heap,
            exn=self.exn,
            pta_query=self.pta_query,
            budget=self.budget,
        )

    def __str__(self) -> str:
        env_str = ", ".join(f"{k}={v}" for k, v in sorted(self.env.items()))
        return f"AIState(env={{{env_str}}}, heap={len(self.heap)} objs)"

