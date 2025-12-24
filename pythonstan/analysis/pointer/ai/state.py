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
        may_be_none: Whether value may be None
        bool_val: Optional boolean value (True/False/None=unknown)
    """
    addrs: FrozenSet['AbstractObject'] = field(default_factory=frozenset)
    str_const: Optional[FrozenSet[str]] = None  # None = unknown, empty = definitely not string
    may_be_none: bool = False
    bool_val: Optional[bool] = None  # None = unknown

    @classmethod
    def bottom(cls) -> 'AbsVal':
        """Create bottom value (unreachable)."""
        return cls()

    @classmethod
    def top(cls) -> 'AbsVal':
        """Create top value (unknown)."""
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
        """Check if this is bottom."""
        return (
            len(self.addrs) == 0 and
            self.str_const is not None and len(self.str_const) == 0 and
            not self.may_be_none and
            self.bool_val is None
        )

    def is_const_str(self) -> bool:
        """Check if this is a constant string (finite set)."""
        return self.str_const is not None and len(self.str_const) > 0

    def is_unknown_str(self) -> bool:
        """Check if string component is unknown."""
        return self.str_const is None

    def get_const_strs(self) -> Optional[FrozenSet[str]]:
        """Get constant strings if enumerable, else None."""
        return self.str_const

    def join(self, other: 'AbsVal') -> 'AbsVal':
        """Join two abstract values (⊔)."""
        # Join addresses
        new_addrs = self.addrs | other.addrs

        # Join string constants
        if self.str_const is None or other.str_const is None:
            new_str = None
        else:
            new_str = self.str_const | other.str_const

        # Join bool
        if self.bool_val is None or other.bool_val is None:
            new_bool = None
        elif self.bool_val == other.bool_val:
            new_bool = self.bool_val
        else:
            new_bool = None

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
        """Check if self ⊑ other."""
        if not self.addrs <= other.addrs:
            return False
        if self.str_const is not None:
            if other.str_const is None:
                pass  # self is more precise
            elif not self.str_const <= other.str_const:
                return False
        if self.may_be_none and not other.may_be_none:
            return False
        if self.bool_val is not None:
            if other.bool_val is None:
                pass
            elif self.bool_val != other.bool_val:
                return False
        return True

    def widen(self, other: 'AbsVal', max_strs: int = 10) -> 'AbsVal':
        """Widen abstract value (∇).
        
        String widening: if too many constants, go to unknown.
        Address widening: handled at state level.
        """
        joined = self.join(other)
        
        # String widening
        if joined.str_const is not None and len(joined.str_const) > max_strs:
            joined = AbsVal(
                addrs=joined.addrs,
                str_const=None,  # Widen to unknown
                may_be_none=joined.may_be_none,
                bool_val=joined.bool_val,
            )
        
        return joined

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
        """
        max_strs = self.budget.max_const_strings if self.budget else 10
        max_addrs = self.budget.max_addr_set_size if self.budget else 50

        # Widen environments
        new_env: Dict[str, AbsVal] = {}
        all_vars = set(self.env.keys()) | set(other.env.keys())
        for v in all_vars:
            v1 = self.env.get(v, AbsVal.bottom())
            v2 = other.env.get(v, AbsVal.bottom())
            widened = v1.widen(v2, max_strs=max_strs)
            
            # Address set widening: if too large, summarize
            if len(widened.addrs) > max_addrs:
                # For now just keep as-is; proper summarization needs alloc-site merging
                pass
            
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
        
        Apply meet to refine after widening.
        """
        # Narrow environments
        new_env: Dict[str, AbsVal] = {}
        for v in self.env:
            if v in other.env:
                new_env[v] = self.env[v].meet(other.env[v])
            else:
                new_env[v] = self.env[v]

        return AIState(
            env=new_env,
            heap=self.heap,  # Don't narrow heap for now
            exn=self.exn,
            pta_query=self.pta_query,
            budget=self.budget,
        )

    def __str__(self) -> str:
        env_str = ", ".join(f"{k}={v}" for k, v in sorted(self.env.items()))
        return f"AIState(env={{{env_str}}}, heap={len(self.heap)} objs)"

