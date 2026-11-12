"""Independent finite-key shape and identity controls at the Native boundary."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from pythonstan.analysis.pointer.kcfa.context import (
    AbstractContext,
    CallSite,
    CallStringContext,
    Ctx,
    FiniteContextAtom,
    HybridContext,
    ObjectContext,
    OrderedBindingToken,
    SummaryContext,
    _origin_sources,
    _source_identity,
    finite_context_atom,
    is_native_context,
    ordered_binding_token,
)
from pythonstan.analysis.pointer.kcfa.context_selector import ContextPolicy, ContextSelector
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite
from pythonstan.analysis.pointer.kcfa.protocol_context import KeywordPartition, ProtocolKeyContext
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from tests.pointer.kcfa.builders import make_scope, make_site


def test_context_keys_forget_helper_suffix_and_keep_real_source_identity() -> None:
    scope = make_scope("finite", "module")
    plain = CallStringContext()
    first = Ctx(plain, scope, Variable("$helper@first", VariableKind.TEMPORARY))
    second = Ctx(plain, scope, Variable("$helper@second", VariableKind.TEMPORARY))
    other_source = Ctx(plain, make_scope("finite", "module"), first.content)
    assert finite_context_atom(first) == finite_context_atom(second)
    assert hash(finite_context_atom(first)) == hash(finite_context_atom(second))
    assert finite_context_atom(first) != finite_context_atom(other_source)
    assert finite_context_atom(Ctx(plain, None, Variable("x"))) == FiniteContextAtom(
        "variable", (None, VariableKind.LOCAL, "x")
    )


def test_ordered_binding_keys_keep_names_positions_and_multiplicity() -> None:
    class KeywordEntries:
        def items(self) -> tuple[tuple[str, str], tuple[str, str]]:
            return (("left", "y"), ("right", "x"))

    left = ordered_binding_token(("x", "x"), (("left", "y"), ("right", "x")))
    assert left.bindings == (("pos:0", "x"), ("pos:1", "x"), ("kw:left", "y"), ("kw:right", "x"))
    assert left != ordered_binding_token(("x",), (("left", "y"), ("right", "x")))
    assert left != ordered_binding_token(("x", "x"), (("right", "x"), ("left", "y")))
    assert finite_context_atom(left) == left
    assert ordered_binding_token(("x", "x"), KeywordEntries()) == left
    with pytest.raises(ValueError):
        ordered_binding_token((("nested",),))


def test_protocol_and_summary_wrappers_project_flat_origins_and_preserve_hash() -> None:
    source = make_site(name="call")
    call = CallSite(source.stmt, "finite")
    partition = KeywordPartition(call, None, "summary", None)
    base = ObjectContext((FiniteContextAtom("object", "old"),), 2)
    protocol = ProtocolKeyContext(base, partition)
    summary = SummaryContext(protocol)
    assert is_native_context(protocol)
    assert hash(protocol) == hash((base, partition))
    assert _origin_sources(summary) == ("old",)
    owner = AbstractObject(summary, make_site(name="owner"))
    assert finite_context_atom(owner) == FiniteContextAtom("object", owner.alloc_site, ("old",))


def test_synthetic_owner_projection_is_bounded_and_does_not_retain_objects() -> None:
    owner = AbstractObject(CallStringContext(), make_site(name="root"))
    for _ in range(24):
        site = AllocSite(("namespace", owner), AllocKind.DICT)
        owner = AbstractObject(ObjectContext((owner,), 2), site)
        atom = finite_context_atom(owner)
    assert isinstance(atom, FiniteContextAtom)
    assert "source_shape_summary" in repr(atom.source)
    assert "AbstractObject(" not in repr(atom.source)


def test_source_projection_retains_finite_structural_carriers_and_unknown_summary() -> None:
    @dataclass(frozen=True)
    class FieldSymbol:
        kind: str = "attribute"
        name: str = "key"
        index: int = 2
        key_type: str = "str"

    source = FieldSymbol()
    assert _source_identity(source) == ("attribute", "key", 2, "str")
    assert _source_identity(SimpleNamespace(alloc_site=source)) == ("attribute", "key", 2, "str")
    assert _source_identity(1j) == ("source_shape_summary", "complex")
    assert _source_identity(object()) == ("source_shape_summary", "object")
    assert finite_context_atom(SimpleNamespace(obj="owner", field="key")) == FiniteContextAtom(
        "field", ("owner", "key")
    )
    with pytest.raises(TypeError):
        finite_context_atom(source)
    with pytest.raises(ValueError):
        FiniteContextAtom("object", source)
    with pytest.raises(ValueError):
        OrderedBindingToken((("pos:0", ("nested",)),))


@pytest.mark.parametrize("policy", list(ContextPolicy))
def test_native_guard_admits_each_policy_and_ordinary_wrapper_composition(
    policy: ContextPolicy,
) -> None:
    base = ContextSelector(policy).empty_context()
    partition = KeywordPartition(CallSite(make_site().stmt), None, "summary", None)
    assert is_native_context(base)
    assert is_native_context(SummaryContext(base))
    assert is_native_context(ProtocolKeyContext(base, partition))
    assert is_native_context(SummaryContext(ProtocolKeyContext(base, partition)))


def test_native_guard_rejects_custom_underlying_context_without_changing_wrapper_api() -> None:
    @dataclass(frozen=True)
    class CustomIntContext(AbstractContext[int]):
        value: int = 0

        def to_string(self) -> str:
            return str(self.value)

        def is_empty(self) -> bool:
            return self.value == 0

        def append(self, value: int) -> "CustomIntContext":
            return CustomIntContext(value)

        def __hash__(self) -> int:
            return hash(self.value)

    custom = CustomIntContext(2)
    partition = KeywordPartition(CallSite(make_site().stmt), None, "summary", None)
    wrapped = ProtocolKeyContext(custom, partition)
    # The standalone generic APIs still accept and operate on this context.
    assert SummaryContext(custom).append(3).inner == CustomIntContext(3)
    assert wrapped.append(3).inner == CustomIntContext(3)
    assert not is_native_context(custom)
    assert not is_native_context(SummaryContext(custom))
    assert not is_native_context(wrapped)
    assert not is_native_context(SummaryContext(wrapped))


def test_native_guard_rejects_a_forged_wrapper_cycle() -> None:
    base = CallStringContext()
    wrapped = SummaryContext(base)
    object.__setattr__(wrapped, "inner", wrapped)
    try:
        assert not is_native_context(wrapped)
    finally:
        object.__setattr__(wrapped, "inner", base)


@pytest.mark.parametrize("call_k", (0, 1, 2, 3))
@pytest.mark.parametrize("obj_depth", (0, 1, 2, 3))
def test_hybrid_pair_base_summary_and_protocol_match_legacy_two_arguments(
    call_k: int, obj_depth: int
) -> None:
    call = CallSite(make_site().stmt)
    owner = AbstractObject(CallStringContext(), make_site())
    partition = KeywordPartition(call, None, "summary", None)
    base = HybridContext((), (), call_k, obj_depth)
    for receiver in (None, owner):
        pair = (call, receiver)
        legacy = base.append(call, receiver)
        direct = base.append(pair)
        assert direct == legacy
        assert hash(direct) == hash(legacy)
        assert repr(direct) == repr(legacy)
        assert SummaryContext(base).append(pair).inner == legacy
        assert ProtocolKeyContext(base, partition).append(pair).inner == legacy
        nested = SummaryContext(ProtocolKeyContext(base, partition)).append(pair)
        assert nested.inner.inner == legacy


def test_hybrid_legacy_missing_and_extra_arguments_still_raise_type_error() -> None:
    call = CallSite(make_site().stmt)
    base = HybridContext()
    with pytest.raises(TypeError):
        base.append()
    with pytest.raises(TypeError):
        base.append(call)
    with pytest.raises(TypeError):
        base.append(call, None, None)


def test_stable_dynamic_native_metadata_preserves_old_public_projection() -> None:
    class DynamicMetadata:
        def __init__(self, **fields: object) -> None:
            self.fields = fields

        def __getattr__(self, name: str) -> object:
            try:
                return self.fields[name]
            except KeyError as error:
                raise AttributeError(name) from error

    class IndexedOrigins:
        def __getitem__(self, index: int) -> object:
            return ("earlier", "latest")[index]

    call = CallSite(make_site().stmt)
    dynamic_context = DynamicMetadata(inner=DynamicMetadata(call_sites=IndexedOrigins()))
    allocation = DynamicMetadata(alloc_site="supported-source", context=dynamic_context)
    expected = FiniteContextAtom("object", "supported-source", ("earlier", "latest"))
    assert finite_context_atom(allocation) == expected
    assert _source_identity(allocation) == "supported-source"
    assert _origin_sources(dynamic_context) == ("earlier", "latest")
    assert _source_identity(DynamicMetadata(kind="field", name="key", index=2, key_type="str")) == (
        "field",
        "key",
        2,
        "str",
    )
    variable = Ctx(CallStringContext(), None, DynamicMetadata(kind="local", name="$x@dynamic"))
    assert finite_context_atom(variable) == FiniteContextAtom("variable", (None, "local", "$x"))
    assert _source_identity(variable) == (None, "local", "$x")
    assert finite_context_atom(DynamicMetadata(obj=allocation, field="key")) == FiniteContextAtom(
        "field", ("supported-source", "key")
    )
    keywords = DynamicMetadata(items=lambda: (("left", call), ("right", allocation)))
    assert ordered_binding_token((), keywords).bindings == (
        ("kw:left", call),
        ("kw:right", expected),
    )
    with pytest.raises(TypeError):
        ordered_binding_token((), DynamicMetadata(items=3))
    malformed = DynamicMetadata(
        alloc_site=DynamicMetadata(kind=object(), name="bad"), context=CallStringContext()
    )
    with pytest.raises(ValueError):
        finite_context_atom(malformed)
