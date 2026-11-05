"""Value-domain composition for AI abstract interpretation.

Defines AbsVal (reduced product of pluggable domains) and common
bridge operations used by the interpreter and heap model.
"""

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    FrozenSet,
    Optional,
    Set,
    Tuple,
)

from .domains.base import DomainRegistry, create_default_registry
from .domains.strings import StrLattice
from .domains.bool import BoolLattice
from .domains.none import NoneLattice
from .domains.ints import IntLattice
from pythonstan.analysis.pointer.kcfa.object import summarize_object

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject

__all__ = [
    "AbsVal",
    "Value",
    "AddrSet",
    "CallableTarget",
    "DomainRegistry",
    "get_default_registry",
    "to_points_to",
    "to_callable_targets",
    "bind_descriptor",
    "configure_registry",
    "STRING_DOMAIN",
    "BOOL_DOMAIN",
    "NONE_DOMAIN",
    "INT_DOMAIN",
    "CALLABLE_DOMAIN",
    "TYPE_DOMAIN",
    "DESCRIPTOR_DOMAIN",
]

STRING_DOMAIN = "string"
BOOL_DOMAIN = "bool"
NONE_DOMAIN = "none"
INT_DOMAIN = "int"
CALLABLE_DOMAIN = "callable"
TYPE_DOMAIN = "type"
DESCRIPTOR_DOMAIN = "descriptor"

AddrSet = FrozenSet["AbstractObject"]

_DEFAULT_REGISTRY: Optional[DomainRegistry] = None

_UNSET = object()


def get_default_registry() -> DomainRegistry:
    """Return the shared default domain registry."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = create_default_registry()
    return _DEFAULT_REGISTRY


@dataclass(frozen=True)
class CallableTarget:
    """Callable target for dynamic call dispatch.

    Attributes:
        callee_obj: KCFA abstract object to invoke
        receiver: Optional receiver value (self/cls binding)
    """

    callee_obj: "AbstractObject"
    receiver: Optional["AbsVal"] = None


# =============================================================================
# AbsVal - Abstract values (reduced product)
# =============================================================================


@dataclass(frozen=True, init=False)
class AbsVal:
    """Abstract value = ⟨AddrSet, DomainComponents⟩.

    Reduced product across pluggable domains with address set component.

    Attributes:
        addrs: Set of abstract objects this value may point to
        components: Domain component mapping (domain_name -> element)
    """

    addrs: AddrSet = field(default_factory=frozenset)
    components: Dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        addrs: Optional[AddrSet] = None,
        components: Optional[Dict[str, Any]] = None,
        *,
        str_const: Any = _UNSET,
        bool_val: Any = _UNSET,
        may_be_none: Any = _UNSET,
    ) -> None:
        """Initialize AbsVal with optional legacy fields."""
        addr_set = frozenset(addrs or ())
        comp_map: Dict[str, Any] = dict(components or {})

        if str_const is not _UNSET:
            if str_const is None:
                comp_map[STRING_DOMAIN] = StrLattice.top()
            else:
                comp_map[STRING_DOMAIN] = StrLattice.from_consts(frozenset(str_const))

        if bool_val is not _UNSET:
            if bool_val is None:
                comp_map[BOOL_DOMAIN] = BoolLattice.top()
            elif bool_val:
                comp_map[BOOL_DOMAIN] = BoolLattice.true_()
            else:
                comp_map[BOOL_DOMAIN] = BoolLattice.false_()

        if may_be_none is not _UNSET:
            if may_be_none:
                comp_map[NONE_DOMAIN] = NoneLattice.top()
            else:
                comp_map[NONE_DOMAIN] = NoneLattice.not_none()

        object.__setattr__(self, "addrs", addr_set)
        object.__setattr__(self, "components", comp_map)
        self.__post_init__()

    def __post_init__(self) -> None:
        registry = get_default_registry()
        normalized: Dict[str, Any] = {}
        for domain in registry.domains:
            if domain.name == NONE_DOMAIN:
                normalized[domain.name] = NoneLattice.not_none()
            else:
                normalized[domain.name] = domain.top()
        for name, elem in self.components.items():
            normalized[name] = elem
        object.__setattr__(self, "components", normalized)

    @classmethod
    def bottom(cls) -> "AbsVal":
        """Create bottom value (unreachable / no information).

        NOTE: Bottom is NOT the same as "unknown" - it means "no possible value".
        For a variable that doesn't exist yet, we use bottom.
        Joining bottom with any value V yields V.
        """
        registry = get_default_registry()
        components = {d.name: d.bottom() for d in registry.domains}
        return cls(addrs=frozenset(), components=components)

    @classmethod
    def top(cls) -> "AbsVal":
        """Create top value (unknown / any possible value)."""
        registry = get_default_registry()
        components = {d.name: d.top() for d in registry.domains}
        return cls(addrs=frozenset(), components=components)

    @classmethod
    def top_non_none(cls) -> "AbsVal":
        """Create top value refined to definitely not None."""
        val = cls.top()
        return val.with_component(NONE_DOMAIN, NoneLattice.not_none())

    @classmethod
    def from_addrs(cls, addrs: AddrSet) -> "AbsVal":
        """Create value from address set."""
        return cls(addrs=addrs)

    @classmethod
    def from_str(cls, s: str) -> "AbsVal":
        """Create value from constant string."""
        return cls(components={STRING_DOMAIN: StrLattice.const(s)})

    @classmethod
    def from_strs(cls, strs: FrozenSet[str]) -> "AbsVal":
        """Create value from set of constant strings."""
        return cls(components={STRING_DOMAIN: StrLattice.from_consts(strs)})

    @classmethod
    def none_val(cls) -> "AbsVal":
        """Create None value."""
        return cls(components={NONE_DOMAIN: NoneLattice.none_()})

    @classmethod
    def bool_true(cls) -> "AbsVal":
        """Create True value."""
        return cls(components={BOOL_DOMAIN: BoolLattice.true_()})

    @classmethod
    def bool_false(cls) -> "AbsVal":
        """Create False value."""
        return cls(components={BOOL_DOMAIN: BoolLattice.false_()})

    @classmethod
    def bool_top(cls) -> "AbsVal":
        """Create unknown boolean value."""
        return cls(components={BOOL_DOMAIN: BoolLattice.top()})

    @classmethod
    def from_int(cls, n: int) -> "AbsVal":
        """Create value from constant integer."""
        return cls(components={INT_DOMAIN: IntLattice.const(n)})

    @classmethod
    def from_ints(cls, ints: FrozenSet[int]) -> "AbsVal":
        """Create value from set of constant integers."""
        return cls(components={INT_DOMAIN: IntLattice.from_consts(ints)})

    def with_component(self, name: str, elem: Any) -> "AbsVal":
        """Return a copy with one component updated."""
        new_components = dict(self.components)
        new_components[name] = elem
        return AbsVal(addrs=self.addrs, components=new_components)

    def with_components(self, components: Dict[str, Any]) -> "AbsVal":
        """Return a copy with multiple components updated."""
        new_components = dict(self.components)
        new_components.update(components)
        return AbsVal(addrs=self.addrs, components=new_components)

    def get_component(self, name: str) -> Any:
        """Get a component by domain name."""
        return self.components.get(name)

    def get_string_lattice(self) -> StrLattice:
        elem = self.components.get(STRING_DOMAIN)
        return elem if isinstance(elem, StrLattice) else StrLattice.top()

    def get_bool_lattice(self) -> BoolLattice:
        elem = self.components.get(BOOL_DOMAIN)
        return elem if isinstance(elem, BoolLattice) else BoolLattice.top()

    def get_none_lattice(self) -> NoneLattice:
        elem = self.components.get(NONE_DOMAIN)
        return elem if isinstance(elem, NoneLattice) else NoneLattice.top()

    def get_int_lattice(self) -> IntLattice:
        elem = self.components.get(INT_DOMAIN)
        return elem if isinstance(elem, IntLattice) else IntLattice.top()

    @property
    def str_const(self) -> Optional[FrozenSet[str]]:
        """Legacy accessor for constant string sets."""
        str_elem = self.get_string_lattice()
        if str_elem.is_enumerable():
            return str_elem.consts
        if str_elem.is_bottom():
            return frozenset()
        return None

    @property
    def bool_val(self) -> Optional[bool]:
        """Legacy accessor for boolean lattice (True/False/None)."""
        bool_elem = self.get_bool_lattice()
        if bool_elem.is_true():
            return True
        if bool_elem.is_false():
            return False
        return None

    @property
    def may_be_none(self) -> Optional[bool]:
        """Legacy accessor for None-ness (True/False/None)."""
        none_elem = self.get_none_lattice()
        if none_elem.is_none():
            return True
        if none_elem.is_not_none():
            return False
        if none_elem.is_top():
            return True
        return None

    def is_bottom(self) -> bool:
        """Check if this is bottom (no possible value)."""
        if len(self.addrs) != 0:
            return False
        registry = get_default_registry()
        for domain in registry.domains:
            elem = self.components.get(domain.name)
            if elem is None or not domain.is_bottom(elem):
                return False
        return True

    def is_const_str(self) -> bool:
        """Check if this is a constant string (finite non-empty set)."""
        str_elem = self.get_string_lattice()
        return str_elem.is_const() and len(str_elem.consts) > 0

    def is_unknown_str(self) -> bool:
        """Check if string component is unknown (top)."""
        return self.get_string_lattice().is_top()

    def is_str_bottom(self) -> bool:
        """Check if string component is bottom (empty set = definitely not a string)."""
        return self.get_string_lattice().is_bottom()

    def get_const_strs(self) -> Optional[FrozenSet[str]]:
        """Get constant strings if enumerable, else None."""
        return self.get_string_lattice().get_consts()

    def filter_string_candidates(self, candidates: FrozenSet[str]) -> FrozenSet[str]:
        """Filter candidate strings using the string lattice.

        Returns the subset of candidates that may equal this value's string component.
        Used for filtering known attribute names when exact enumeration isn't possible.

        Args:
            candidates: Set of candidate strings to filter

        Returns:
            Filtered subset of candidates that satisfy may_equal
        """
        return self.get_string_lattice().filter_candidates(candidates)

    def may_equal_str(self, s: str) -> bool:
        """Check if string component may equal a specific string.

        Args:
            s: String to check

        Returns:
            True if the string component may equal s
        """
        return self.get_string_lattice().may_equal(s)

    def join(self, other: "AbsVal") -> "AbsVal":
        """Join two abstract values (⊔).

        Bottom is the identity for join: bottom ⊔ V = V.
        """
        if self.is_bottom():
            return other
        if other.is_bottom():
            return self

        new_addrs = self.addrs | other.addrs
        registry = get_default_registry()
        new_components: Dict[str, Any] = {}
        for domain in registry.domains:
            name = domain.name
            a = self.components.get(name, domain.top())
            b = other.components.get(name, domain.top())
            new_components[name] = domain.join(a, b)

        return AbsVal(addrs=new_addrs, components=new_components)

    def meet(self, other: "AbsVal") -> "AbsVal":
        """Meet two abstract values (⊓)."""
        new_addrs = self.addrs & other.addrs
        registry = get_default_registry()
        new_components: Dict[str, Any] = {}
        for domain in registry.domains:
            name = domain.name
            a = self.components.get(name, domain.top())
            b = other.components.get(name, domain.top())
            new_components[name] = domain.meet(a, b)

        return AbsVal(addrs=new_addrs, components=new_components)

    def leq(self, other: "AbsVal") -> bool:
        """Check if self ⊑ other.

        Component-wise partial order with domain lattices.
        """
        if not self.addrs <= other.addrs:
            return False
        registry = get_default_registry()
        for domain in registry.domains:
            name = domain.name
            a = self.components.get(name, domain.top())
            b = other.components.get(name, domain.top())
            if not domain.leq(a, b):
                return False
        return True

    def widen(
        self,
        other: "AbsVal",
        max_addrs: int = 50,
        max_strs: Optional[int] = None,
    ) -> "AbsVal":
        """Widen abstract value (∇).

        From ai_spec.md §10.1:
        - Address widening: if too many addresses, summarize by alloc-site.
        """
        registry = get_default_registry()
        new_components: Dict[str, Any] = {}
        for domain in registry.domains:
            name = domain.name
            a = self.components.get(name, domain.top())
            b = other.components.get(name, domain.top())
            widened = domain.widen(a, b)
            if (
                max_strs is not None
                and name == STRING_DOMAIN
                and isinstance(widened, StrLattice)
                and widened.is_enumerable()
                and len(widened.consts) > max_strs
            ):
                widened = StrLattice.top()
            new_components[name] = widened

        new_addrs = self.addrs | other.addrs
        if len(new_addrs) > max_addrs:
            new_addrs = self._summarize_addrs_by_alloc_site(new_addrs)

        return AbsVal(addrs=new_addrs, components=new_components)

    @staticmethod
    def _summarize_addrs_by_alloc_site(addrs: AddrSet) -> AddrSet:
        """Summarize addresses via summary(site, trunc(ctx)) as per ai_spec §10.1."""
        summaries = {}
        for obj in addrs:
            site = getattr(obj, "alloc_site", None)
            if site is None:
                summaries[obj] = summarize_object(obj)
                continue
            if site not in summaries:
                summaries[site] = summarize_object(obj)
        return frozenset(summaries.values())

    def with_addrs(self, addrs: AddrSet) -> "AbsVal":
        """Return copy with new address set."""
        return AbsVal(addrs=addrs, components=self.components)

    def fingerprint(self) -> Tuple[Any, ...]:
        """Return a hashable fingerprint for caching."""
        component_items = tuple(sorted(self.components.items(), key=lambda x: x[0]))
        return (self.addrs, component_items)

    def __str__(self) -> str:
        parts = []
        if self.addrs:
            parts.append(f"addrs={{{len(self.addrs)} objs}})")
        registry = get_default_registry()
        for domain in registry.domains:
            elem = self.components.get(domain.name)
            if elem is None:
                continue
            if not domain.is_top(elem) and not domain.is_bottom(elem):
                parts.append(f"{domain.name}={elem}")
        return f"AbsVal({', '.join(parts)})"


Value = AbsVal


def to_points_to(val: AbsVal) -> AddrSet:
    """Return KCFA-compatible points-to set for a value."""
    return val.addrs


# Re-export from binding module for backward compatibility
from .domains.binding import to_callable_targets, bind_descriptor


def configure_registry(max_ho_tokens: Optional[int] = None) -> None:
    """Update registry configuration for domain-specific caps.
    
    Applies the cap to all token-based domains for consistency.
    """
    registry = get_default_registry()
    if max_ho_tokens is not None:
        # Apply to all token-based domains
        for domain_name in (
            CALLABLE_DOMAIN,
            TYPE_DOMAIN,
            DESCRIPTOR_DOMAIN,
        ):
            domain = registry.get(domain_name)
            if domain is not None and hasattr(domain, "max_tokens"):
                domain.max_tokens = max_ho_tokens
