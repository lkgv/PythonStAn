"""Value-domain composition for AI abstract interpretation.

Defines AbsVal (reduced product of pluggable domains) and common
bridge operations used by the interpreter and heap model.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, Optional, Set, Tuple

from pythonstan.compat.legacy_ai.domains.base import DomainRegistry, create_default_registry
from pythonstan.analysis.domains.strings import StrLattice
from pythonstan.analysis.domains.bool import BoolLattice
from pythonstan.analysis.domains.none import NoneLattice
from pythonstan.analysis.domains.ints import IntLattice
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, summarize_object

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
    """Return immutable compatibility defaults, never session configuration."""
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
        addr_top: Arbitrary additional addresses are possible; ``addrs`` then
            retains discovered witnesses rather than an exhaustive set.
    """

    addrs: AddrSet = field(default_factory=frozenset)
    components: Dict[str, Any] = field(default_factory=dict)
    addr_top: bool = False
    registry: DomainRegistry = field(default=None, compare=False, repr=False)
    # None is the conservative compatibility projection of an untagged legacy
    # constraint product. Exact factories track actual scalar alternatives.
    scalar_kinds: Optional[FrozenSet[str]] = None
    boolean_values: Optional[BoolLattice] = None

    def __init__(
        self,
        addrs: Optional[AddrSet] = None,
        components: Optional[Dict[str, Any]] = None,
        *,
        addr_top: bool = False,
        registry: Optional[DomainRegistry] = None,
        scalar_kinds: Optional[FrozenSet[str]] = None,
        boolean_values: Optional[BoolLattice] = None,
        str_const: Any = _UNSET,
        bool_val: Any = _UNSET,
        may_be_none: Any = _UNSET,
    ) -> None:
        """Initialize AbsVal with optional legacy fields."""
        if type(addr_top) is not bool:
            raise TypeError("addr_top must be a bool")
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
        object.__setattr__(self, "addr_top", addr_top)
        object.__setattr__(self, "registry", registry or get_default_registry())
        object.__setattr__(self, "scalar_kinds", None if scalar_kinds is None else frozenset(scalar_kinds))
        object.__setattr__(self, "boolean_values", boolean_values)
        self.__post_init__()

    def __post_init__(self) -> None:
        registry = self.registry
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
    def bottom(cls, registry=None) -> "AbsVal":
        """Create bottom value (unreachable / no information).

        NOTE: Bottom is NOT the same as "unknown" - it means "no possible value".
        For a variable that doesn't exist yet, we use bottom.
        Joining bottom with any value V yields V.
        """
        registry = registry or get_default_registry()
        components = {d.name: d.bottom() for d in registry.domains}
        return cls(addrs=frozenset(), components=components, registry=registry, scalar_kinds=frozenset(),
                   boolean_values=BoolLattice.bottom())

    @classmethod
    def top(cls, registry=None) -> "AbsVal":
        """Create top value (unknown / any possible value)."""
        registry = registry or get_default_registry()
        components = {d.name: d.top() for d in registry.domains}
        return cls(addrs=frozenset(), components=components, addr_top=True, registry=registry)

    @classmethod
    def top_non_none(cls) -> "AbsVal":
        """Create top value refined to definitely not None."""
        val = cls.top()
        return val.with_component(NONE_DOMAIN, NoneLattice.not_none())

    @classmethod
    def from_addrs(cls, addrs: AddrSet) -> "AbsVal":
        """Address witnesses with explicit scalar projections, never invented sorts."""
        if not addrs:
            return cls.bottom()
        if any(getattr(obj, "is_top_value", False) for obj in addrs):
            return cls.top().with_addrs(addrs)
        value = cls.bottom()
        from pythonstan.analysis.pointer.kcfa.object import ConstantObject
        for obj in addrs:
            if isinstance(obj, ConstantObject):
                literal = obj.value
                if literal is None:
                    projection = cls.none_val()
                elif type(literal) is bool:
                    projection = cls.bool_true() if literal else cls.bool_false()
                elif type(literal) is int:
                    projection = cls.from_int(literal)
                elif type(literal) is str:
                    projection = cls.from_str(literal)
                else:
                    # This legacy scalar product has no float/bytes domain.
                    projection = cls.top_non_none()
                value = value.join(projection)
        return cls(addrs=addrs, components=value.components, scalar_kinds=value.scalar_kinds,
                   boolean_values=value.boolean_values, addr_top=value.addr_top)

    @classmethod
    def _scalar(cls, kind, elem, truthiness=None):
        registry = get_default_registry()
        components = {domain.name: domain.bottom() for domain in registry.domains}
        components[kind] = elem
        if kind != NONE_DOMAIN:
            components[NONE_DOMAIN] = NoneLattice.not_none()
        if kind != BOOL_DOMAIN and truthiness is not None:
            components[BOOL_DOMAIN] = BoolLattice.true_() if truthiness else BoolLattice.false_()
        return cls(components=components, scalar_kinds=frozenset({kind}), registry=registry,
                   boolean_values=elem if kind == BOOL_DOMAIN else BoolLattice.bottom())

    @classmethod
    def from_str(cls, s: str) -> "AbsVal":
        return cls._scalar(STRING_DOMAIN, StrLattice.const(s), bool(s))

    @classmethod
    def from_strs(cls, strs: FrozenSet[str]) -> "AbsVal":
        if not strs:
            return cls.bottom()
        truth = bool(next(iter(strs))) if all(bool(s) == bool(next(iter(strs))) for s in strs) else None
        result = cls._scalar(STRING_DOMAIN, StrLattice.from_consts(strs), truth)
        return result if truth is not None else result.with_component(BOOL_DOMAIN, BoolLattice.top())

    @classmethod
    def none_val(cls) -> "AbsVal":
        return cls._scalar(NONE_DOMAIN, NoneLattice.none_(), False)

    @classmethod
    def bool_true(cls) -> "AbsVal":
        return cls._scalar(BOOL_DOMAIN, BoolLattice.true_())

    @classmethod
    def bool_false(cls) -> "AbsVal":
        return cls._scalar(BOOL_DOMAIN, BoolLattice.false_())

    @classmethod
    def bool_top(cls) -> "AbsVal":
        return cls._scalar(BOOL_DOMAIN, BoolLattice.top())

    @classmethod
    def from_int(cls, n: int) -> "AbsVal":
        return cls._scalar(INT_DOMAIN, IntLattice.const(n), n != 0)

    @classmethod
    def from_ints(cls, ints: FrozenSet[int]) -> "AbsVal":
        if not ints:
            return cls.bottom()
        result = cls._scalar(INT_DOMAIN, IntLattice.from_consts(ints), True if 0 not in ints else False if ints == frozenset({0}) else None)
        return result if 0 not in ints or ints == frozenset({0}) else result.with_component(BOOL_DOMAIN, BoolLattice.top())

    def with_registry(self, registry: DomainRegistry) -> "AbsVal":
        if registry is self.registry:
            return self
        return AbsVal(self.addrs, self.components, addr_top=self.addr_top, registry=registry,
                      scalar_kinds=self.scalar_kinds, boolean_values=self.boolean_values)

    def with_component(self, name: str, elem: Any) -> "AbsVal":
        """Return a copy with one component updated."""
        new_components = dict(self.components)
        new_components[name] = elem
        kinds = self.scalar_kinds
        if kinds == frozenset() and not self.addrs and name in (STRING_DOMAIN, INT_DOMAIN, BOOL_DOMAIN, NONE_DOMAIN):
            domain = self.registry.get(name)
            if domain is not None and not domain.is_bottom(elem):
                kinds = frozenset({name})
        booleans = self.boolean_values
        if name == BOOL_DOMAIN and kinds is not None:
            booleans = (elem if not self.scalar_kinds and BOOL_DOMAIN in kinds
                        else self.registry.get(BOOL_DOMAIN).meet(self.get_bool_values(), elem) if BOOL_DOMAIN in kinds
                        else BoolLattice.bottom())
        return AbsVal(addrs=self.addrs, components=new_components, addr_top=self.addr_top,
                      registry=self.registry, scalar_kinds=kinds, boolean_values=booleans)

    def with_components(self, components: Dict[str, Any]) -> "AbsVal":
        """Return a copy with multiple components updated."""
        new_components = dict(self.components)
        new_components.update(components)
        return AbsVal(addrs=self.addrs, components=new_components, addr_top=self.addr_top,
                      registry=self.registry, scalar_kinds=self.scalar_kinds, boolean_values=self.boolean_values)

    def get_component(self, name: str) -> Any:
        """Get a component by domain name."""
        return self.components.get(name)

    def get_string_lattice(self) -> StrLattice:
        if self.scalar_kinds is not None and STRING_DOMAIN not in self.scalar_kinds:
            return StrLattice.bottom()
        elem = self.components.get(STRING_DOMAIN)
        return elem if isinstance(elem, StrLattice) else StrLattice.top()

    def get_bool_lattice(self) -> BoolLattice:
        elem = self.components.get(BOOL_DOMAIN)
        return elem if isinstance(elem, BoolLattice) else BoolLattice.top()

    def get_none_lattice(self) -> NoneLattice:
        elem = self.components.get(NONE_DOMAIN)
        return elem if isinstance(elem, NoneLattice) else NoneLattice.top()

    def get_int_lattice(self) -> IntLattice:
        if self.scalar_kinds is not None and INT_DOMAIN not in self.scalar_kinds:
            return IntLattice.bottom()
        elem = self.components.get(INT_DOMAIN)
        return elem if isinstance(elem, IntLattice) else IntLattice.top()

    def get_bool_values(self) -> BoolLattice:
        """Actual bool values, separate from the historical truthiness facet."""
        if self.scalar_kinds is not None and BOOL_DOMAIN not in self.scalar_kinds:
            return BoolLattice.bottom()
        return self.boolean_values if self.boolean_values is not None else self.get_bool_lattice()

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
        if self.scalar_kinds is not None and NONE_DOMAIN not in self.scalar_kinds:
            return False
        none_elem = self.get_none_lattice()
        if none_elem.is_none():
            return True
        if none_elem.is_not_none():
            return False
        if none_elem.is_top():
            return True
        return None

    def is_bottom(self) -> bool:
        """No actual scalar/reference/token alternative; truthiness is not a value."""
        if self.addr_top or self.addrs:
            return False
        if self.scalar_kinds is not None:
            if STRING_DOMAIN in self.scalar_kinds and not self.get_string_lattice().is_bottom():
                return False
            if INT_DOMAIN in self.scalar_kinds and not self.get_int_lattice().is_bottom:
                return False
            if BOOL_DOMAIN in self.scalar_kinds and not self.get_bool_values().is_bottom():
                return False
            if NONE_DOMAIN in self.scalar_kinds and self.get_none_lattice().val.value in ('none', 'top'):
                return False
            return all(self.registry.get(name).is_bottom(self.components[name])
                       for name in (CALLABLE_DOMAIN, TYPE_DOMAIN, DESCRIPTOR_DOMAIN)
                       if self.registry.get(name) is not None)
        # An untagged old constraint product is projected conservatively.
        return all(domain.is_bottom(self.components.get(domain.name, domain.top()))
                   for domain in self.registry.domains)

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
        registry = self.registry
        new_components: Dict[str, Any] = {}
        for domain in registry.domains:
            name = domain.name
            a = self.components.get(name, domain.top())
            b = other.components.get(name, domain.top())
            new_components[name] = domain.join(a, b)

        return AbsVal(addrs=new_addrs, components=new_components,
                      addr_top=self.addr_top or other.addr_top, registry=self.registry,
                      scalar_kinds=None if self.scalar_kinds is None or other.scalar_kinds is None
                      else self.scalar_kinds | other.scalar_kinds,
                      boolean_values=registry.get(BOOL_DOMAIN).join(self.get_bool_values(), other.get_bool_values()))

    def meet(self, other: "AbsVal") -> "AbsVal":
        """Meet two abstract values (⊓)."""
        # A summarized allocation covers its context-specific objects. Plain
        # set intersection would incorrectly discard those objects in narrowing.
        if self.addr_top and other.addr_top:
            new_addrs = self.addrs | other.addrs
        elif self.addr_top:
            new_addrs = other.addrs
        elif other.addr_top:
            new_addrs = self.addrs
        else:
            new_addrs = frozenset(
                obj for obj in self.addrs if self._addr_is_covered(obj, other.addrs)
            ) | frozenset(
                obj for obj in other.addrs if self._addr_is_covered(obj, self.addrs)
            )
        registry = self.registry
        new_components: Dict[str, Any] = {}
        for domain in registry.domains:
            name = domain.name
            a = self.components.get(name, domain.top())
            b = other.components.get(name, domain.top())
            new_components[name] = domain.meet(a, b)

        return AbsVal(addrs=new_addrs, components=new_components,
                      addr_top=self.addr_top and other.addr_top, registry=self.registry,
                      scalar_kinds=(other.scalar_kinds if self.scalar_kinds is None else self.scalar_kinds
                                    if other.scalar_kinds is None else self.scalar_kinds & other.scalar_kinds),
                      boolean_values=registry.get(BOOL_DOMAIN).meet(self.get_bool_values(), other.get_bool_values()))

    def leq(self, other: "AbsVal") -> bool:
        """Check if self ⊑ other.

        Component-wise partial order with domain lattices.
        """
        if other.scalar_kinds is not None and (self.scalar_kinds is None
                or not self.scalar_kinds <= other.scalar_kinds):
            return False
        if not self.registry.get(BOOL_DOMAIN).leq(self.get_bool_values(), other.get_bool_values()):
            return False
        if not other.addr_top:
            if self.addr_top or not all(self._addr_is_covered(obj, other.addrs)
                                        or self._constant_is_covered(obj, other)
                                        for obj in self.addrs):
                return False
        registry = self.registry
        for domain in registry.domains:
            name = domain.name
            a = self.components.get(name, domain.top())
            b = other.components.get(name, domain.top())
            if not domain.leq(a, b):
                return False
        return True

    @staticmethod
    def _constant_is_covered(obj, value):
        """A builtin scalar facet covers redundant ConstantObject witnesses.

        No user object, callable, mutable reference or subclass is discharged by
        scalar membership. This is concretization inclusion, not singleton or
        identity evidence for a mutable heap object.
        """
        from pythonstan.analysis.pointer.kcfa.object import ConstantObject
        if not isinstance(obj, ConstantObject):
            return False
        literal = obj.value
        if literal is None:
            return value.may_be_none is True
        if type(literal) is bool:
            booleans = value.get_bool_values()
            return booleans.may_be_true() if literal else booleans.may_be_false()
        if type(literal) is int:
            return value.get_int_lattice().may_contain(literal)
        if type(literal) is str:
            return value.get_string_lattice().may_equal(literal)
        return False

    @staticmethod
    def _addr_is_covered(obj: "AbstractObject", addrs: AddrSet) -> bool:
        return obj in addrs or (
            isinstance(obj, AbstractObject) and summarize_object(obj) in addrs
        )

    def widen(
        self,
        other: "AbsVal",
        max_addrs: int = 50,
        max_strs: Optional[int] = None,
        max_ho_tokens: Optional[int] = None,
    ) -> "AbsVal":
        """Widen abstract value (∇).

        From ai_spec.md §10.1:
        - Address widening: if too many addresses, summarize by alloc-site.
        """
        registry = self.registry
        new_components: Dict[str, Any] = {}
        for domain in registry.domains:
            name = domain.name
            a = self.components.get(name, domain.top())
            b = other.components.get(name, domain.top())
            if max_ho_tokens is not None and hasattr(domain, "max_tokens"):
                # Engine-local budgets must not mutate the shared registry or
                # let a separately configured engine change this widening.
                widened = domain.join(a, b)
                if len(getattr(widened, "tokens", ())) > max_ho_tokens:
                    widened = domain.top()
            elif max_strs is not None and name == STRING_DOMAIN:
                from pythonstan.analysis.domains.strings import StringDomain
                widened = StringDomain(max_consts=max_strs).widen(a, b)
            else:
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

        return AbsVal(addrs=new_addrs, components=new_components,
                      addr_top=self.addr_top or other.addr_top, registry=self.registry,
                      scalar_kinds=None if self.scalar_kinds is None or other.scalar_kinds is None
                      else self.scalar_kinds | other.scalar_kinds,
                      boolean_values=registry.get(BOOL_DOMAIN).join(self.get_bool_values(), other.get_bool_values()))

    @staticmethod
    def _summarize_addrs_by_alloc_site(addrs: AddrSet) -> AddrSet:
        """Summarize addresses via summary(site, trunc(ctx)) as per ai_spec §10.1."""
        # Do not collapse distinct object payloads (e.g. bound receivers) just
        # because they share an allocation site. Summarization changes context.
        summaries = set()
        legacy_sites = set()
        for obj in addrs:
            if isinstance(obj, AbstractObject):
                summaries.add(summarize_object(obj))
            else:
                # Preserve the historical allocation-site protocol used by
                # external object adapters; concrete KCFA objects above retain
                # every payload field when their contexts are summarized.
                site = getattr(obj, "alloc_site", None)
                if site is None or not hasattr(obj, "context"):
                    summaries.add(obj)
                elif site not in legacy_sites:
                    legacy_sites.add(site)
                    summaries.add(summarize_object(obj))
        return frozenset(summaries)

    def with_addrs(self, addrs: AddrSet, *, addr_top: Optional[bool] = None) -> "AbsVal":
        """Replace finite witnesses, preserving unknown addresses by default.

        A trusted transfer may supply addr_top=False when its refinement proof
        excludes other addresses. Removing witnesses alone is not that proof.
        """
        effective_top = self.addr_top if addr_top is None else addr_top
        return AbsVal(addrs=addrs, components=self.components, addr_top=effective_top,
                      registry=self.registry, scalar_kinds=self.scalar_kinds, boolean_values=self.boolean_values)

    def fingerprint(self) -> Tuple[Any, ...]:
        """Return a hashable fingerprint for caching."""
        component_items = tuple(sorted(self.components.items(), key=lambda x: x[0]))
        return (self.addr_top, self.addrs, self.scalar_kinds, self.get_bool_values(), component_items)

    def __str__(self) -> str:
        parts = []
        if self.addr_top:
            parts.append("addrs=top")
        if self.addrs:
            parts.append(f"addrs={{{len(self.addrs)} objs}})")
        registry = self.registry
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
    if val.addr_top:
        raise ValueError("Address Top cannot be materialized as a finite points-to set")
    return val.addrs


# Re-export from binding module for backward compatibility
from pythonstan.compat.legacy_ai.domains.binding import to_callable_targets, bind_descriptor


def configure_registry(max_ho_tokens: Optional[int] = None) -> DomainRegistry:
    """Return isolated compatibility settings; never mutate shared defaults."""
    if max_ho_tokens is None:
        return create_default_registry()
    from types import SimpleNamespace
    return create_default_registry(SimpleNamespace(max_ho_tokens=max_ho_tokens))
