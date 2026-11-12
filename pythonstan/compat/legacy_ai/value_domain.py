"""Value-domain composition for AI abstract interpretation.

Defines AbsVal (reduced product of pluggable domains) and common
bridge operations used by the interpreter and heap model.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Optional, override

from pythonstan.analysis.domains.base import DomainRegistry, RegisteredDomain
from pythonstan.analysis.domains.bool import BoolLattice
from pythonstan.analysis.domains.ints import IntLattice
from pythonstan.analysis.domains.none import NoneLattice
from pythonstan.analysis.domains.strings import StrLattice
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, summarize_object
from pythonstan.compat.legacy_ai.domains.base import create_default_registry
from pythonstan.compat.legacy_ai.domains.binding import bind_descriptor, to_callable_targets

__all__ = [
    "AbsVal",
    "Value",
    "LegacyDomainContractError",
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

AddrSet = frozenset["AbstractObject"]

_DEFAULT_REGISTRY: DomainRegistry | None = None

_UNSET = object()


class LegacyDomainContractError(TypeError):
    """A fixed Native projection cannot consume a registered representation."""


def _boolean_domain(registry: DomainRegistry) -> RegisteredDomain:
    """Resolve the actual provider without inferring its element type from its name."""
    domain = registry.get(BOOL_DOMAIN)
    if domain is None:
        raise LegacyDomainContractError("Native boolean projection requires a bool provider")
    return domain


def _boolean_element(value: object) -> BoolLattice:
    """Validate each provider result at the actual fixed-representation consumer."""
    if not isinstance(value, BoolLattice):
        raise LegacyDomainContractError("Native boolean projection requires BoolLattice")
    return value


def _legacy_string_constants(value: object) -> frozenset[str]:
    """Decode the legacy iterable argument without leaking unchecked elements."""
    if not isinstance(value, Iterable):
        raise TypeError("str_const must be an iterable of strings or None")
    constants: set[str] = set()
    items: Iterable[object] = value
    for item in items:
        if not isinstance(item, str):
            raise TypeError("str_const elements must be strings")
        constants.add(item)
    return frozenset(constants)


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
    # This is the actual heterogeneous registry boundary: each provider checks
    # its own representation before an operation; builtin projections check too.
    components: dict[str, object] = field(default_factory=dict)
    addr_top: bool = False
    registry: DomainRegistry = field(
        default_factory=get_default_registry, compare=False, repr=False
    )
    # None is the conservative compatibility projection of an untagged legacy
    # constraint product. Exact factories track actual scalar alternatives.
    scalar_kinds: frozenset[str] | None = None
    boolean_values: BoolLattice | None = None

    def __init__(
        self,
        addrs: AddrSet | None = None,
        components: dict[str, object] | None = None,
        *,
        addr_top: bool = False,
        registry: DomainRegistry | None = None,
        scalar_kinds: frozenset[str] | None = None,
        boolean_values: BoolLattice | None = None,
        str_const: object = _UNSET,
        bool_val: object = _UNSET,
        may_be_none: object = _UNSET,
    ) -> None:
        """Initialize AbsVal with optional legacy fields."""
        if type(addr_top) is not bool:
            raise TypeError("addr_top must be a bool")
        addr_set = frozenset(addrs or ())
        comp_map: dict[str, object] = dict(components or {})

        if str_const is not _UNSET:
            if str_const is None:
                comp_map[STRING_DOMAIN] = StrLattice.top()
            else:
                comp_map[STRING_DOMAIN] = StrLattice.from_consts(
                    _legacy_string_constants(str_const)
                )

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
        object.__setattr__(
            self, "scalar_kinds", None if scalar_kinds is None else frozenset(scalar_kinds)
        )
        object.__setattr__(self, "boolean_values", boolean_values)
        self.__post_init__()

    def __post_init__(self) -> None:
        registry = self.registry
        normalized: dict[str, object] = {}
        for domain in registry.domains:
            if domain.name == NONE_DOMAIN:
                normalized[domain.name] = NoneLattice.not_none()
            else:
                normalized[domain.name] = domain.top()
        for name, elem in self.components.items():
            normalized[name] = elem
        object.__setattr__(self, "components", normalized)

    @classmethod
    def bottom(cls, registry: DomainRegistry | None = None) -> "AbsVal":
        """Create bottom value (unreachable / no information).

        NOTE: Bottom is NOT the same as "unknown" - it means "no possible value".
        For a variable that doesn't exist yet, we use bottom.
        Joining bottom with any value V yields V.
        """
        registry = registry or get_default_registry()
        components = {d.name: d.bottom() for d in registry.domains}
        return cls(
            addrs=frozenset(),
            components=components,
            registry=registry,
            scalar_kinds=frozenset(),
            boolean_values=BoolLattice.bottom(),
        )

    @classmethod
    def top(cls, registry: DomainRegistry | None = None) -> "AbsVal":
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
                elif type(literal) is float:
                    # Finite float witnesses have an exact Native value even
                    # though this compatibility product has no float facet.
                    projection = cls.bottom()
                else:
                    projection = cls.top_non_none()
                value = value.join(projection)
        return cls(
            addrs=addrs,
            components=value.components,
            scalar_kinds=value.scalar_kinds,
            boolean_values=value.boolean_values,
            addr_top=value.addr_top,
        )

    @classmethod
    def _scalar(cls, kind: str, elem: object, truthiness: bool | None = None) -> "AbsVal":
        registry = get_default_registry()
        components = {domain.name: domain.bottom() for domain in registry.domains}
        components[kind] = elem
        if kind != NONE_DOMAIN:
            components[NONE_DOMAIN] = NoneLattice.not_none()
        if kind != BOOL_DOMAIN and truthiness is not None:
            components[BOOL_DOMAIN] = BoolLattice.true_() if truthiness else BoolLattice.false_()
        return cls(
            components=components,
            scalar_kinds=frozenset({kind}),
            registry=registry,
            boolean_values=_boolean_element(elem) if kind == BOOL_DOMAIN else BoolLattice.bottom(),
        )

    @classmethod
    def from_str(cls, s: str) -> "AbsVal":
        return cls._scalar(STRING_DOMAIN, StrLattice.const(s), bool(s))

    @classmethod
    def from_strs(cls, strs: frozenset[str]) -> "AbsVal":
        if not strs:
            return cls.bottom()
        truth = (
            bool(next(iter(strs))) if all(bool(s) == bool(next(iter(strs))) for s in strs) else None
        )
        result = cls._scalar(STRING_DOMAIN, StrLattice.from_consts(strs), truth)
        return (
            result if truth is not None else result.with_component(BOOL_DOMAIN, BoolLattice.top())
        )

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
    def from_ints(cls, ints: frozenset[int]) -> "AbsVal":
        if not ints:
            return cls.bottom()
        result = cls._scalar(
            INT_DOMAIN,
            IntLattice.from_consts(ints),
            True if 0 not in ints else False if ints == frozenset({0}) else None,
        )
        return (
            result
            if 0 not in ints or ints == frozenset({0})
            else result.with_component(BOOL_DOMAIN, BoolLattice.top())
        )

    def with_registry(self, registry: DomainRegistry) -> "AbsVal":
        if registry is self.registry:
            return self
        return AbsVal(
            self.addrs,
            self.components,
            addr_top=self.addr_top,
            registry=registry,
            scalar_kinds=self.scalar_kinds,
            boolean_values=self.boolean_values,
        )

    def with_component(self, name: str, elem: object) -> "AbsVal":
        """Return a copy with one component updated."""
        new_components: dict[str, object] = dict(self.components)
        new_components[name] = elem
        kinds = self.scalar_kinds
        if (
            kinds == frozenset()
            and not self.addrs
            and name in (STRING_DOMAIN, INT_DOMAIN, BOOL_DOMAIN, NONE_DOMAIN)
        ):
            domain = self.registry.get(name)
            if domain is not None and not domain.is_bottom(elem):
                kinds = frozenset({name})
        booleans = self.boolean_values
        if name == BOOL_DOMAIN and kinds is not None:
            if not self.scalar_kinds and BOOL_DOMAIN in kinds:
                booleans = _boolean_element(elem)
            elif BOOL_DOMAIN in kinds:
                booleans = _boolean_element(
                    _boolean_domain(self.registry).meet(self.get_bool_values(), elem)
                )
            else:
                booleans = BoolLattice.bottom()
        return AbsVal(
            addrs=self.addrs,
            components=new_components,
            addr_top=self.addr_top,
            registry=self.registry,
            scalar_kinds=kinds,
            boolean_values=booleans,
        )

    def with_components(self, components: dict[str, object]) -> "AbsVal":
        """Return a copy with multiple components updated."""
        new_components = dict(self.components)
        new_components.update(components)
        return AbsVal(
            addrs=self.addrs,
            components=new_components,
            addr_top=self.addr_top,
            registry=self.registry,
            scalar_kinds=self.scalar_kinds,
            boolean_values=self.boolean_values,
        )

    def get_component(self, name: str) -> object:
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
    def str_const(self) -> frozenset[str] | None:
        """Legacy accessor for constant string sets."""
        str_elem = self.get_string_lattice()
        if str_elem.is_enumerable():
            return str_elem.consts
        if str_elem.is_bottom():
            return frozenset()
        return None

    @property
    def bool_val(self) -> bool | None:
        """Legacy accessor for boolean lattice (True/False/None)."""
        bool_elem = self.get_bool_lattice()
        if bool_elem.is_true():
            return True
        if bool_elem.is_false():
            return False
        return None

    @property
    def may_be_none(self) -> bool | None:
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
            if NONE_DOMAIN in self.scalar_kinds and self.get_none_lattice().val.value in (
                "none",
                "top",
            ):
                return False
            for name in (CALLABLE_DOMAIN, TYPE_DOMAIN, DESCRIPTOR_DOMAIN):
                domain = self.registry.get(name)
                if domain is not None and not domain.is_bottom(self.components[name]):
                    return False
            return True
        # An untagged old constraint product is projected conservatively.
        return all(
            domain.is_bottom(self.components.get(domain.name, domain.top()))
            for domain in self.registry.domains
        )

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

    def get_const_strs(self) -> frozenset[str] | None:
        """Get constant strings if enumerable, else None."""
        return self.get_string_lattice().get_consts()

    def filter_string_candidates(self, candidates: frozenset[str]) -> frozenset[str]:
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
        new_components: dict[str, object] = {}
        for domain in registry.domains:
            name = domain.name
            a = self.components.get(name, domain.top())
            b = other.components.get(name, domain.top())
            new_components[name] = domain.join(a, b)

        return AbsVal(
            addrs=new_addrs,
            components=new_components,
            addr_top=self.addr_top or other.addr_top,
            registry=self.registry,
            scalar_kinds=None
            if self.scalar_kinds is None or other.scalar_kinds is None
            else self.scalar_kinds | other.scalar_kinds,
            boolean_values=_boolean_element(
                _boolean_domain(registry).join(self.get_bool_values(), other.get_bool_values())
            ),
        )

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
            ) | frozenset(obj for obj in other.addrs if self._addr_is_covered(obj, self.addrs))
        registry = self.registry
        new_components: dict[str, object] = {}
        for domain in registry.domains:
            name = domain.name
            a = self.components.get(name, domain.top())
            b = other.components.get(name, domain.top())
            new_components[name] = domain.meet(a, b)

        return AbsVal(
            addrs=new_addrs,
            components=new_components,
            addr_top=self.addr_top and other.addr_top,
            registry=self.registry,
            scalar_kinds=(
                other.scalar_kinds
                if self.scalar_kinds is None
                else self.scalar_kinds
                if other.scalar_kinds is None
                else self.scalar_kinds & other.scalar_kinds
            ),
            boolean_values=_boolean_element(
                _boolean_domain(registry).meet(self.get_bool_values(), other.get_bool_values())
            ),
        )

    def leq(self, other: "AbsVal") -> bool:
        """Check if self ⊑ other.

        Component-wise partial order with domain lattices.
        """
        if other.scalar_kinds is not None and (
            self.scalar_kinds is None or not self.scalar_kinds <= other.scalar_kinds
        ):
            return False
        if not _boolean_domain(self.registry).leq(self.get_bool_values(), other.get_bool_values()):
            return False
        if not other.addr_top and (
            self.addr_top
            or not all(
                self._addr_is_covered(obj, other.addrs) or self._constant_is_covered(obj, other)
                for obj in self.addrs
            )
        ):
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
    def _constant_is_covered(obj: "AbstractObject", value: "AbsVal") -> bool:
        """A builtin scalar facet covers redundant ConstantObject witnesses.

        No user object, callable, mutable reference or subclass is discharged by
        scalar membership. This is concretization inclusion, not singleton or
        identity evidence for a mutable heap object.
        """
        from pythonstan.analysis.pointer.kcfa.object import ConstantObject

        if not isinstance(obj, ConstantObject):
            return False
        literal: object = obj.value
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
        return obj in addrs or (isinstance(obj, AbstractObject) and summarize_object(obj) in addrs)

    def widen(
        self,
        other: "AbsVal",
        max_addrs: int = 50,
        max_strs: int | None = None,
        max_ho_tokens: int | None = None,
    ) -> "AbsVal":
        """Widen abstract value (∇).

        From ai_spec.md §10.1:
        - Address widening: if too many addresses, summarize by alloc-site.
        """
        registry = self.registry
        new_components: dict[str, object] = {}
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

                if not isinstance(a, StrLattice) or not isinstance(b, StrLattice):
                    raise LegacyDomainContractError(
                        "Native string capacity projection requires StrLattice"
                    )
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

        return AbsVal(
            addrs=new_addrs,
            components=new_components,
            addr_top=self.addr_top or other.addr_top,
            registry=self.registry,
            scalar_kinds=None
            if self.scalar_kinds is None or other.scalar_kinds is None
            else self.scalar_kinds | other.scalar_kinds,
            boolean_values=_boolean_element(
                _boolean_domain(registry).join(self.get_bool_values(), other.get_bool_values())
            ),
        )

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

    def with_addrs(self, addrs: AddrSet, *, addr_top: bool | None = None) -> "AbsVal":
        """Replace finite witnesses, preserving unknown addresses by default.

        A trusted transfer may supply addr_top=False when its refinement proof
        excludes other addresses. Removing witnesses alone is not that proof.
        """
        effective_top = self.addr_top if addr_top is None else addr_top
        return AbsVal(
            addrs=addrs,
            components=self.components,
            addr_top=effective_top,
            registry=self.registry,
            scalar_kinds=self.scalar_kinds,
            boolean_values=self.boolean_values,
        )

    def fingerprint(self) -> tuple[object, ...]:
        """Return a hashable fingerprint for caching."""
        component_items = tuple(sorted(self.components.items(), key=lambda x: x[0]))
        return (
            self.addr_top,
            self.addrs,
            self.scalar_kinds,
            self.get_bool_values(),
            component_items,
        )

    @override
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


def configure_registry(max_ho_tokens: int | None = None) -> DomainRegistry:
    """Return isolated compatibility settings; never mutate shared defaults."""
    if max_ho_tokens is None:
        return create_default_registry()
    from types import SimpleNamespace

    return create_default_registry(SimpleNamespace(max_ho_tokens=max_ho_tokens))
