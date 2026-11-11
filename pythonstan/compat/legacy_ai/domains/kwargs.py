"""KwShape: Keyword argument shape modeling for abstract interpretation.

This module provides **kwargs shape modeling for call precision:
- KwShape: Represents possible shapes of **kwargs dictionaries
- KwShapeDisj: Disjunction of KwShape alternatives
- Utilities for extracting kwargs from call sites and propagating through wrappers

Key features:
- Uses SlotMap-based structure for string key tracking
- Integrates with TupleDisj for combined *args/**kwargs forwarding
- Supports precise keyword binding when key set is bounded
- Falls back to conservative handling for unbounded keys

Design notes:
- KwShape uses SlotMap conventions for presence (bounded = no unknown keys)
- KwShapeDisj allows multiple possible shapes (from conditionals)
- ArgKwShape combines TupleDisj + KwShapeDisj for full call shape

Wrapper pattern support:
    def wrapper(*args, **kwargs):
        return target(*args, **kwargs)
    
- Extract ArgKwShape at call to wrapper
- Propagate through wrapper body
- Apply ArgKwShape at call to target

Integration with CallSemantics:
- When binding arguments, if KwShape has bounded keys, bind precisely
- Unknown keys still go to **kwargs catch-all if present
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, List, Optional, Set, Tuple, Union

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.context import AbstractContext, Scope
    from pythonstan.compat.legacy_ai.state import AIState
    from pythonstan.compat.legacy_ai.value_domain import AbsVal
    from pythonstan.compat.legacy_ai.domains.mapslots import SlotMap, Presence
    from pythonstan.compat.legacy_ai.domains.tuples import TupleDisj

__all__ = [
    "KwShape",
    "KwShapeDisj",
    "ArgKwShape",
    "materialize_kwargs_shape",
    "expand_kwargs",
]


# =============================================================================
# KwShape - Single kwargs shape
# =============================================================================

@dataclass(frozen=True)
class KwShape:
    """Represents a single possible shape of **kwargs.
    
    Tracks which keyword argument names may be present with their values.
    Uses presence tracking to distinguish:
    - Definitely present keys (PRESENT)
    - Maybe present keys (MAYBE)
    - Definitely absent keys (ABSENT for keys refined by conditions)
    
    Attributes:
        keys: Dict from string key to (value, presence) pair
        has_unknown: Whether there may be additional unknown keys
        unknown_val: Value for unknown keys (if has_unknown)
    """
    keys: Dict[str, Tuple["AbsVal", "Presence"]] = field(default_factory=dict)
    has_unknown: bool = False
    unknown_val: Optional["AbsVal"] = None
    
    def is_empty(self) -> bool:
        """Check if no keys are tracked."""
        return len(self.keys) == 0 and not self.has_unknown
    
    def is_bounded(self) -> bool:
        """Check if the set of possible keys is bounded (no unknown keys)."""
        return not self.has_unknown
    
    def get_value(self, key: str) -> Optional["AbsVal"]:
        """Get value for a specific key."""
        if key in self.keys:
            return self.keys[key][0]
        if self.has_unknown:
            return self.unknown_val
        return None
    
    def get_presence(self, key: str) -> "Presence":
        """Get presence for a specific key."""
        from pythonstan.compat.legacy_ai.domains.mapslots import Presence
        if key in self.keys:
            return self.keys[key][1]
        if self.has_unknown:
            return Presence.MAYBE
        return Presence.ABSENT
    
    def get_present_keys(self) -> FrozenSet[str]:
        """Get keys that may be present."""
        from pythonstan.compat.legacy_ai.domains.mapslots import Presence
        return frozenset(
            k for k, (_, p) in self.keys.items() if p.may_be_present()
        )
    
    def get_definite_keys(self) -> FrozenSet[str]:
        """Get keys that are definitely present."""
        from pythonstan.compat.legacy_ai.domains.mapslots import Presence
        return frozenset(
            k for k, (_, p) in self.keys.items() if p.is_definitely_present()
        )
    
    def with_key(
        self,
        key: str,
        val: "AbsVal",
        pres: "Presence",
    ) -> "KwShape":
        """Return copy with a key added/updated."""
        new_keys = dict(self.keys)
        if key in new_keys:
            # Join with existing
            old_val, old_pres = new_keys[key]
            new_keys[key] = (old_val.join(val), old_pres.join(pres))
        else:
            new_keys[key] = (val, pres)
        return KwShape(
            keys=new_keys,
            has_unknown=self.has_unknown,
            unknown_val=self.unknown_val,
        )
    
    def with_unknown(self, val: "AbsVal") -> "KwShape":
        """Return copy with unknown keys enabled."""
        new_unknown = val
        if self.unknown_val is not None:
            new_unknown = self.unknown_val.join(val)
        return KwShape(
            keys=self.keys,
            has_unknown=True,
            unknown_val=new_unknown,
        )
    
    def join(self, other: "KwShape") -> "KwShape":
        """Join two KwShape."""
        from pythonstan.compat.legacy_ai.domains.mapslots import Presence
        from pythonstan.compat.legacy_ai.value_domain import AbsVal
        
        # Union of keys
        all_keys = set(self.keys.keys()) | set(other.keys.keys())
        new_keys: Dict[str, Tuple[AbsVal, Presence]] = {}
        
        for key in all_keys:
            if key in self.keys and key in other.keys:
                v1, p1 = self.keys[key]
                v2, p2 = other.keys[key]
                new_keys[key] = (v1.join(v2), p1.join(p2))
            elif key in self.keys:
                v1, p1 = self.keys[key]
                # Key only in self - might not exist in other
                new_keys[key] = (v1, Presence.MAYBE)
            else:
                v2, p2 = other.keys[key]
                new_keys[key] = (v2, Presence.MAYBE)
        
        # Handle unknown
        new_has_unknown = self.has_unknown or other.has_unknown
        new_unknown_val = None
        if self.unknown_val is not None and other.unknown_val is not None:
            new_unknown_val = self.unknown_val.join(other.unknown_val)
        elif self.unknown_val is not None:
            new_unknown_val = self.unknown_val
        elif other.unknown_val is not None:
            new_unknown_val = other.unknown_val
        
        return KwShape(
            keys=new_keys,
            has_unknown=new_has_unknown,
            unknown_val=new_unknown_val,
        )
    
    def widen(self, other: "KwShape", max_keys: int = 20) -> "KwShape":
        """Widen two KwShape."""
        from pythonstan.compat.legacy_ai.domains.mapslots import Presence
        from pythonstan.compat.legacy_ai.value_domain import AbsVal
        
        joined = self.join(other)
        
        if len(joined.keys) <= max_keys:
            return joined
        
        # Too many keys - spill some to unknown
        # Keep definitely present keys first
        definite = [(k, v) for k, (v, p) in joined.keys.items() 
                    if p.is_definitely_present()]
        maybe = [(k, v) for k, (v, p) in joined.keys.items()
                 if not p.is_definitely_present()]
        
        new_keys: Dict[str, Tuple[AbsVal, Presence]] = {}
        spilled_val = joined.unknown_val or AbsVal.bottom()
        
        for k, v in definite[:max_keys]:
            new_keys[k] = (v, Presence.PRESENT)
        
        remaining_slots = max_keys - len(new_keys)
        for k, v in maybe[:remaining_slots]:
            new_keys[k] = (v, Presence.MAYBE)
        
        # Spill the rest
        for k, v in maybe[remaining_slots:]:
            spilled_val = spilled_val.join(v)
        
        return KwShape(
            keys=new_keys,
            has_unknown=True,
            unknown_val=spilled_val,
        )
    
    @classmethod
    def empty(cls) -> "KwShape":
        """Create empty kwargs shape (no keywords)."""
        return cls(keys={}, has_unknown=False, unknown_val=None)
    
    @classmethod
    def top(cls) -> "KwShape":
        """Create top kwargs shape (unknown keys with unknown values)."""
        from pythonstan.compat.legacy_ai.value_domain import AbsVal
        return cls(keys={}, has_unknown=True, unknown_val=AbsVal.top())
    
    def __str__(self) -> str:
        keys_str = ", ".join(sorted(self.get_present_keys())[:5])
        if len(self.keys) > 5:
            keys_str += f", ... ({len(self.keys) - 5} more)"
        unknown_str = ", **" if self.has_unknown else ""
        return f"KwShape({{{keys_str}{unknown_str}}})"


# =============================================================================
# KwShapeDisj - Disjunction of KwShape alternatives
# =============================================================================

@dataclass(frozen=True)
class KwShapeDisj:
    """Disjunction of KwShape alternatives.
    
    Represents multiple possible kwargs shapes from conditional branches
    or joins of different call sites.
    
    Attributes:
        alts: Tuple of alternative KwShape
    """
    alts: Tuple[KwShape, ...] = field(default_factory=tuple)
    
    def is_empty(self) -> bool:
        """Check if no alternatives."""
        return len(self.alts) == 0
    
    def is_single(self) -> bool:
        """Check if single alternative."""
        return len(self.alts) == 1
    
    def is_bounded(self) -> bool:
        """Check if all alternatives have bounded keys."""
        return all(alt.is_bounded() for alt in self.alts)
    
    def get_all_possible_keys(self) -> FrozenSet[str]:
        """Get all keys that may be present in any alternative."""
        keys: Set[str] = set()
        for alt in self.alts:
            keys.update(alt.get_present_keys())
        return frozenset(keys)
    
    def get_common_keys(self) -> FrozenSet[str]:
        """Get keys that are definitely present in all alternatives."""
        if not self.alts:
            return frozenset()
        result = self.alts[0].get_definite_keys()
        for alt in self.alts[1:]:
            result &= alt.get_definite_keys()
        return result
    
    def join(self, other: "KwShapeDisj", max_alts: int = 4) -> "KwShapeDisj":
        """Join two KwShapeDisj."""
        combined = list(self.alts) + list(other.alts)
        
        if len(combined) <= max_alts:
            return KwShapeDisj(alts=tuple(combined))
        
        # Too many alternatives - merge
        return _merge_kwshape_alts(combined, max_alts)
    
    def widen(self, other: "KwShapeDisj", max_alts: int = 4) -> "KwShapeDisj":
        """Widen KwShapeDisj."""
        return self.join(other, max_alts)
    
    @classmethod
    def empty(cls) -> "KwShapeDisj":
        """Create empty disjunction (no kwargs)."""
        return cls(alts=(KwShape.empty(),))
    
    @classmethod
    def single(cls, shape: KwShape) -> "KwShapeDisj":
        """Create from single shape."""
        return cls(alts=(shape,))
    
    @classmethod
    def top(cls) -> "KwShapeDisj":
        """Create top disjunction (unknown kwargs)."""
        return cls(alts=(KwShape.top(),))
    
    def __str__(self) -> str:
        return f"KwShapeDisj({len(self.alts)} alts)"


def _merge_kwshape_alts(alts: List[KwShape], max_alts: int) -> KwShapeDisj:
    """Merge KwShape alternatives to stay within cap."""
    if len(alts) <= max_alts:
        return KwShapeDisj(alts=tuple(alts))
    
    # Strategy: merge shapes with similar key sets
    # For simplicity, just join all into one
    result = alts[0]
    for alt in alts[1:]:
        result = result.join(alt)
    
    return KwShapeDisj(alts=(result,))


# =============================================================================
# ArgKwShape - Combined positional and keyword shape
# =============================================================================

@dataclass(frozen=True)
class ArgKwShape:
    """Combined *args and **kwargs shape.
    
    Represents the full shape of arguments at a call site or being
    forwarded through a wrapper.
    
    Attributes:
        args: TupleDisj for positional arguments (*args)
        kwargs: KwShapeDisj for keyword arguments (**kwargs)
    """
    args: Optional["TupleDisj"] = None
    kwargs: Optional["KwShapeDisj"] = None
    
    def has_args(self) -> bool:
        """Check if positional args shape is available."""
        return self.args is not None and not self.args.is_empty()
    
    def has_kwargs(self) -> bool:
        """Check if kwargs shape is available."""
        return self.kwargs is not None and not self.kwargs.is_empty()
    
    def is_bounded(self) -> bool:
        """Check if both args and kwargs shapes are bounded."""
        args_bounded = self.args is None or all(
            isinstance(alt, tuple) or (hasattr(alt, 'length') and alt.length <= 8)
            for alt in (self.args.alts if hasattr(self.args, 'alts') else [])
        )
        kwargs_bounded = self.kwargs is None or self.kwargs.is_bounded()
        return args_bounded and kwargs_bounded
    
    def join(self, other: "ArgKwShape") -> "ArgKwShape":
        """Join two ArgKwShape."""
        new_args = None
        if self.args is not None and other.args is not None:
            new_args = self.args.join(other.args)
        elif self.args is not None:
            new_args = self.args
        elif other.args is not None:
            new_args = other.args
        
        new_kwargs = None
        if self.kwargs is not None and other.kwargs is not None:
            new_kwargs = self.kwargs.join(other.kwargs)
        elif self.kwargs is not None:
            new_kwargs = self.kwargs
        elif other.kwargs is not None:
            new_kwargs = other.kwargs
        
        return ArgKwShape(args=new_args, kwargs=new_kwargs)
    
    @classmethod
    def empty(cls) -> "ArgKwShape":
        """Create empty shape (no args, no kwargs)."""
        return cls(args=None, kwargs=KwShapeDisj.empty())
    
    @classmethod
    def top(cls) -> "ArgKwShape":
        """Create top shape (unknown args and kwargs)."""
        return cls(args=None, kwargs=KwShapeDisj.top())
    
    def __str__(self) -> str:
        parts = []
        if self.args is not None:
            parts.append(f"*args={self.args}")
        if self.kwargs is not None:
            parts.append(f"**kwargs={self.kwargs}")
        return f"ArgKwShape({', '.join(parts)})"


# =============================================================================
# Materialization from dict objects
# =============================================================================

def materialize_kwargs_shape(
    dict_val: "AbsVal",
    state: "AIState",
    max_keys: int = 20,
) -> KwShape:
    """Materialize KwShape from a dict abstract value.
    
    Extracts key-value pairs from dict objects in the abstract value.
    
    Args:
        dict_val: Abstract value representing the kwargs dict
        state: Current AI state
        max_keys: Maximum keys to track
        
    Returns:
        KwShape representing the possible kwargs
    """
    from pythonstan.compat.legacy_ai.value_domain import AbsVal
    from pythonstan.compat.legacy_ai.heap import AIAddr
    from pythonstan.compat.legacy_ai.domains.mapslots import Presence
    
    result = KwShape.empty()
    has_any = False
    
    for obj in dict_val.addrs:
        try:
            ai_addr = AIAddr.from_kcfa_obj(obj)
            ai_obj = state.get_obj(ai_addr)
            
            if ai_obj is not None:
                has_any = True
                slots = ai_obj.slots
                
                # Extract string keys from partitions
                from pythonstan.compat.legacy_ai.domains.mapslots import keysig_kind
                for sig, slot in slots.partitions.items():
                    if keysig_kind(sig) == "str":  # String key
                        key = sig[1]
                        if len(result.keys) < max_keys:
                            pres = Presence.PRESENT if slot.pres.is_definitely_present() else Presence.MAYBE
                            result = result.with_key(key, slot.val, pres)
                
                # Handle default bucket (unknown keys)
                if not slots.default.val.is_bottom():
                    result = result.with_unknown(slots.default.val)
        except Exception:
            # Object doesn't have slots - mark as unknown
            result = result.with_unknown(AbsVal.top())
    
    if not has_any and dict_val.addrs:
        # Have addresses but no dict info - conservative
        return KwShape.top()
    
    return result


def expand_kwargs(
    shape: KwShape,
) -> Optional[Dict[str, "AbsVal"]]:
    """Expand KwShape into explicit key-value dict.
    
    Only works if shape is bounded (no unknown keys).
    
    Args:
        shape: KwShape to expand
        
    Returns:
        Dict mapping key names to values, or None if unbounded
    """
    if not shape.is_bounded():
        return None
    
    result: Dict[str, "AbsVal"] = {}
    for key, (val, pres) in shape.keys.items():
        if pres.may_be_present():
            result[key] = val
    
    return result


def extract_call_shape(
    arg_vals: List[Tuple[str, "AbsVal"]],
    state: "AIState",
) -> ArgKwShape:
    """Extract ArgKwShape from call arguments.
    
    Identifies *args and **kwargs arguments and materializes their shapes.
    
    Args:
        arg_vals: List of (name, value) argument pairs
        state: Current AI state
        
    Returns:
        ArgKwShape representing the call shape
    """
    from pythonstan.compat.legacy_ai.domains.tuples import TupleDisj, materialize_tuple_disj
    from pythonstan.compat.legacy_ai.domains.mapslots import Presence
    
    args_shape: Optional[TupleDisj] = None
    kwargs_shape: Optional[KwShapeDisj] = None
    
    for name, val in arg_vals:
        if name.startswith("*") and not name.startswith("**"):
            # *args - materialize tuple shape
            args_shape = materialize_tuple_disj(
                val, state, None, None,
                max_len=8, max_alts=4
            )
        elif name.startswith("**"):
            # **kwargs - materialize kwargs shape
            kw_shape = materialize_kwargs_shape(val, state, max_keys=20)
            kwargs_shape = KwShapeDisj.single(kw_shape)
    
    return ArgKwShape(args=args_shape, kwargs=kwargs_shape)
