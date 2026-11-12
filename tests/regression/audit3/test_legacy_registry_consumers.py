"""Fixed Native projections validate actual dynamically registered results."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal, assert_never, override

import pytest

from pythonstan.analysis.domains.base import Domain, DomainRegistry
from pythonstan.analysis.domains.bool import BoolDomain, BoolLattice, BoolVal
from pythonstan.analysis.domains.ints import IntDomain, IntLattice
from pythonstan.analysis.pointer.kcfa.context import CallSite, CallStringContext
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite
from pythonstan.compat.legacy_ai.domains.callable import CallableDomain, CallableVal, FunctionRef
from pythonstan.compat.legacy_ai.domains.type_domain import ClassRef, TypeDomain, TypeVal
from pythonstan.compat.legacy_ai.heap import AIAddr, ContainerSlots
from pythonstan.compat.legacy_ai.value_domain import AbsVal, LegacyDomainContractError
from pythonstan.ir.ir_statements import IRAstStmt, IRCall
from tests.regression.audit3.test_domain_typing import Finite, UnionProvider

type BooleanOperation = Literal["join", "meet", "widen"]


@dataclass(frozen=True)
class CustomBoolean(BoolLattice):
    """A compatible provider's intermediate representation retains its payload."""

    marker: str = "custom"


class CustomBooleanProvider(BoolDomain):
    def __init__(self) -> None:
        self.calls: list[str] = []

    @override
    def join(self, a: BoolLattice, b: BoolLattice) -> BoolLattice:
        self.calls.append("join")
        return CustomBoolean(super().join(a, b).val, "registered-join")

    @override
    def meet(self, a: BoolLattice, b: BoolLattice) -> BoolLattice:
        self.calls.append("meet")
        return CustomBoolean(super().meet(a, b).val, "registered-meet")


@dataclass(frozen=True)
class ForeignElement:
    label: str


type ForeignBoolean = BoolLattice | ForeignElement


class ForeignBooleanProvider(Domain[ForeignBoolean]):
    """The registry accepts its declared middle variant; Native cannot store it."""

    @property
    @override
    def name(self) -> str:
        return "bool"

    @override
    def element_types(self) -> tuple[type[ForeignBoolean], ...]:
        return (BoolLattice, ForeignElement)

    @override
    def bottom(self) -> ForeignBoolean:
        return BoolLattice.bottom()

    @override
    def top(self) -> ForeignBoolean:
        return BoolLattice.top()

    @override
    def join(self, a: ForeignBoolean, b: ForeignBoolean) -> ForeignBoolean:
        return ForeignElement("registered-join")

    @override
    def meet(self, a: ForeignBoolean, b: ForeignBoolean) -> ForeignBoolean:
        return ForeignElement("registered-meet")

    @override
    def leq(self, a: ForeignBoolean, b: ForeignBoolean) -> bool:
        return a == b


class CustomIntegerProvider(IntDomain):
    def __init__(self) -> None:
        super().__init__()
        self.widened: list[tuple[IntLattice, IntLattice]] = []

    @override
    def widen(self, old: IntLattice, new: IntLattice) -> IntLattice:
        self.widened.append((old, new))
        return IntLattice.top()


type ForeignInteger = IntLattice | ForeignElement


class ForeignIntegerProvider(Domain[ForeignInteger]):
    @property
    @override
    def name(self) -> str:
        return "int"

    @override
    def element_types(self) -> tuple[type[ForeignInteger], ...]:
        return (IntLattice, ForeignElement)

    @override
    def bottom(self) -> ForeignInteger:
        return IntLattice.bottom()

    @override
    def top(self) -> ForeignInteger:
        return IntLattice.top()

    @override
    def join(self, a: ForeignInteger, b: ForeignInteger) -> ForeignInteger:
        if isinstance(a, IntLattice) and isinstance(b, IntLattice):
            return IntDomain().join(a, b)
        return ForeignElement("registered-join")

    @override
    def meet(self, a: ForeignInteger, b: ForeignInteger) -> ForeignInteger:
        if isinstance(a, IntLattice) and isinstance(b, IntLattice):
            return IntDomain().meet(a, b)
        return ForeignElement("registered-meet")

    @override
    def leq(self, a: ForeignInteger, b: ForeignInteger) -> bool:
        if isinstance(a, IntLattice) and isinstance(b, IntLattice):
            return IntDomain().leq(a, b)
        return a == b

    @override
    def widen(self, old: ForeignInteger, new: ForeignInteger) -> ForeignInteger:
        return ForeignElement("registered-widen")


def _registry() -> DomainRegistry:
    registry = DomainRegistry()
    registry.register(BoolDomain())
    registry.register(IntDomain())
    return registry


def _boolean(registry: DomainRegistry, value: BoolLattice) -> AbsVal:
    return AbsVal(
        components={"bool": value},
        registry=registry,
        scalar_kinds=frozenset({"bool"}),
        boolean_values=value,
    )


def _operate(operation: BooleanOperation, left: AbsVal, right: AbsVal) -> AbsVal:
    match operation:
        case "join":
            return left.join(right)
        case "meet":
            return left.meet(right)
        case "widen":
            return left.widen(right)
        case _:
            assert_never(operation)


@pytest.mark.parametrize("operation", ["join", "meet", "widen"])
def test_boolean_projection_executes_actual_replacement(operation: BooleanOperation) -> None:
    registry = _registry()
    provider = CustomBooleanProvider()
    left = _boolean(registry, BoolLattice.true_())
    right = _boolean(registry, BoolLattice.false_())
    # Replacement happens after the product values exist: a name/constructor
    # cannot justify silently caching a builtin provider or its result type.
    registry.register(provider)
    result = _operate(operation, left, right)
    expected = "registered-meet" if operation == "meet" else "registered-join"
    assert isinstance(result.boolean_values, CustomBoolean)
    assert result.boolean_values.marker == expected
    assert provider.calls.count("meet" if operation == "meet" else "join") == 2


def test_component_projection_keeps_compatible_middle_variant() -> None:
    registry = _registry()
    provider = CustomBooleanProvider()
    registry.register(provider)
    initial = _boolean(registry, BoolLattice.true_())
    result = initial.with_component("bool", BoolLattice.false_())
    assert result.boolean_values == CustomBoolean(BoolVal.BOTTOM, "registered-meet")
    assert provider.calls == ["meet"]
    bottom = AbsVal(components={}, registry=registry, scalar_kinds=frozenset())
    custom = CustomBoolean(BoolVal.TRUE, "direct-compatible-middle")
    assert bottom.with_component("bool", custom).boolean_values is custom


def test_other_registered_finite_union_is_preserved_by_real_product_operations() -> None:
    registry = _registry()
    registry.register(UnionProvider())
    a, b = Finite(frozenset({1, 2})), Finite(frozenset({2, 3}))
    left = AbsVal(components={"union-control": a, "bool": BoolLattice.bottom()}, registry=registry)
    right = AbsVal(components={"union-control": b, "bool": BoolLattice.bottom()}, registry=registry)
    assert left.join(right).get_component("union-control") == Finite(frozenset({1, 2, 3}))
    assert left.meet(right).get_component("union-control") == Finite(frozenset({2}))
    assert left.widen(right).get_component("union-control") == Finite(frozenset({1, 2, 3}))


@pytest.mark.parametrize("operation", ["join", "meet", "widen"])
def test_incompatible_declared_boolean_variant_rejected_at_consumer(
    operation: BooleanOperation,
) -> None:
    registry = _registry()
    registry.register(ForeignBooleanProvider())
    provider = registry.get("bool")
    assert provider is not None
    assert provider.join(BoolLattice.true_(), BoolLattice.false_()) == ForeignElement(
        "registered-join"
    )
    left = _boolean(registry, BoolLattice.true_())
    right = _boolean(registry, BoolLattice.false_())
    with pytest.raises(LegacyDomainContractError, match="requires BoolLattice"):
        _operate(operation, left, right)
    assert left.boolean_values == BoolLattice.true_()


def test_incompatible_component_rejected_without_guessing_from_name() -> None:
    registry = _registry()
    registry.register(ForeignBooleanProvider())
    with pytest.raises(LegacyDomainContractError, match="requires BoolLattice"):
        _boolean(registry, BoolLattice.true_()).with_component("bool", BoolLattice.false_())
    bottom = AbsVal(components={}, registry=registry, scalar_kinds=frozenset())
    with pytest.raises(LegacyDomainContractError, match="requires BoolLattice"):
        bottom.with_component("bool", ForeignElement("declared-middle"))


def test_missing_boolean_provider_is_explicit_but_optional_domains_still_skip() -> None:
    registry = DomainRegistry()
    registry.register(IntDomain())
    left = _boolean(registry, BoolLattice.true_())
    right = _boolean(registry, BoolLattice.false_())
    for operation in ("join", "meet", "widen"):
        with pytest.raises(LegacyDomainContractError, match="requires a bool provider"):
            _operate(operation, left, right)
    with pytest.raises(LegacyDomainContractError, match="requires a bool provider"):
        left.leq(right)
    empty = AbsVal(components={}, registry=registry, scalar_kinds=frozenset())
    assert empty.is_bottom()
    assert empty.join(left) is left


def test_container_length_executes_actual_integer_replacement() -> None:
    registry = _registry()
    provider = CustomIntegerProvider()
    registry.register(provider)
    bucket = _boolean(registry, BoolLattice.true_())
    old, new = IntLattice.const(3), IntLattice.const(4)
    left, right = (
        ContainerSlots(elem_bucket=bucket, length=old),
        ContainerSlots(elem_bucket=bucket, length=new),
    )
    assert left.widen(right).length == IntLattice.top()
    assert (old, new) in provider.widened
    assert left.length == old


def test_container_rejects_incompatible_integer_middle_variant() -> None:
    registry = _registry()
    registry.register(ForeignIntegerProvider())
    bucket = _boolean(registry, BoolLattice.true_())
    left = ContainerSlots(elem_bucket=bucket, length=IntLattice.const(3))
    right = ContainerSlots(elem_bucket=bucket, length=IntLattice.const(4))
    provider = registry.get("int")
    assert provider is not None
    assert provider.widen(left.length, right.length) == ForeignElement("registered-widen")
    with pytest.raises(LegacyDomainContractError, match="length requires IntLattice"):
        left.widen(right)
    assert left.length == IntLattice.const(3)


def test_missing_integer_provider_retains_historical_builtin_length_fallback() -> None:
    registry = DomainRegistry()
    registry.register(BoolDomain())
    bucket = _boolean(registry, BoolLattice.true_())
    old, new = IntLattice.const(3), IntLattice.const(4)
    result = ContainerSlots(elem_bucket=bucket, length=old).widen(
        ContainerSlots(elem_bucket=bucket, length=new)
    )
    assert result.length == IntDomain().widen(old, new)


@pytest.mark.parametrize("key", [None, False, True, 2, 2.5, "name"])
def test_real_tracked_scalar_key_variants_survive_container_operations(
    key: None | bool | int | float | str,
) -> None:
    value = AbsVal.from_int(7)
    slots = ContainerSlots().set_index(key, value)
    assert key in slots.tracked_indices
    assert slots.get_index(key).get_int_lattice() == value.get_int_lattice()
    assert key in slots.copy().tracked_indices
    assert key in slots.join(ContainerSlots()).tracked_indices
    assert key in slots.widen(ContainerSlots()).tracked_indices


@pytest.mark.parametrize("constants", ["ab", ["a", "b"], ("a", "b"), frozenset({"a", "b"})])
def test_legacy_string_constructor_decodes_real_iterable(constants: object) -> None:
    assert AbsVal(str_const=constants).get_string_lattice().get_consts() == frozenset({"a", "b"})


def test_legacy_string_constructor_rejects_invalid_elements_at_boundary() -> None:
    with pytest.raises(TypeError, match="iterable of strings"):
        AbsVal(str_const=4)
    with pytest.raises(TypeError, match="elements must be strings"):
        AbsVal(str_const=["a", 4])


def test_heap_address_preserves_real_keyword_partition_context() -> None:
    from pythonstan.analysis.pointer.kcfa.protocol_context import (
        KeywordPartition,
        ProtocolKeyContext,
    )

    statement = ast.parse("consume()").body[0]
    assert isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
    operation = CallSite(IRCall(statement.value), "subject", 0)
    partition = KeywordPartition(operation, None, "summary", None)
    context = ProtocolKeyContext(CallStringContext(), partition)
    obj = AbstractObject(context, AllocSite("container", AllocKind.LIST))
    assert AIAddr.from_kcfa_obj(obj).context is context


@pytest.mark.parametrize("operation", ["join", "meet", "widen"])
@pytest.mark.parametrize("left", list(BoolVal))
@pytest.mark.parametrize("right", list(BoolVal))
def test_builtin_boolean_projection_parity(
    operation: BooleanOperation, left: BoolVal, right: BoolVal
) -> None:
    registry = _registry()
    a, b = BoolLattice(left), BoolLattice(right)
    actual = _operate(operation, _boolean(registry, a), _boolean(registry, b))
    expected = BoolDomain().meet(a, b) if operation == "meet" else BoolDomain().join(a, b)
    assert actual.get_bool_values() == expected


@dataclass(frozen=True)
class ForeignAddress:
    kind: AllocKind


def test_reduction_accepts_opaque_input_but_only_builds_real_native_tokens() -> None:
    registry = _registry()
    type_domain, callable_domain = TypeDomain(), CallableDomain()
    foreign: frozenset[object] = frozenset(
        {ForeignAddress(AllocKind.CLASS), ForeignAddress(AllocKind.FUNCTION)}
    )
    assert type_domain.on_reduce_value("v", foreign, TypeVal.bottom(), registry).is_bottom()
    assert callable_domain.on_reduce_value("v", foreign, CallableVal.bottom(), registry).is_bottom()
    class_object = AbstractObject(
        CallStringContext(), AllocSite(IRAstStmt(ast.Pass()), AllocKind.CLASS)
    )
    function_object = AbstractObject(CallStringContext(), AllocSite("builtin", AllocKind.BUILTIN))
    addresses: frozenset[object] = frozenset({class_object, function_object})
    assert type_domain.on_reduce_value("v", addresses, TypeVal.bottom(), registry) == (
        TypeVal.from_tokens(frozenset({ClassRef(class_object)}))
    )
    assert callable_domain.on_reduce_value("v", addresses, CallableVal.bottom(), registry) == (
        CallableVal.from_tokens(frozenset({FunctionRef(function_object)}))
    )
