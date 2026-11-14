"""Engine-neutral reduced scalar/reference values with run-local operations.

Object and cell identities are opaque strings.  No pointer engine or singleton is
imported here.  Integer and string operations reuse the public scalar lattices.
"""

from __future__ import annotations

import ast
import math
import operator
import struct
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import reduce
from typing import Protocol, override

from pythonstan.analysis.contracts.cost_model import visit_json_text
from pythonstan.analysis.contracts.identity import ObjectId
from pythonstan.analysis.contracts.json_data import JsonObject, JsonScalar, JsonValue
from pythonstan.analysis.contracts.value_wire import canonical_value_document

from .ints import IntDomain, IntLattice
from .reabstract import IdentityArena
from .relations import KeyValueRelation, PartitionToken
from .strings import StringDomain, StrLattice

type ValueEqualityKey = tuple[
    IntLattice,
    StrLattice,
    frozenset[bool],
    tuple[bytes, ...],
    bool,
    frozenset[ObjectId],
    frozenset[str],
    bool,
    bool,
    frozenset[PartitionToken],
]


class ScalarOperator(Protocol):
    """One host scalar primitive; unsupported operand pairs raise TypeError.

    The object result is immediately classified by Value.constant. It also
    accommodates Python primitives such as power that can produce complex.
    """

    def __call__(self, left: object, right: object, /) -> object: ...


def _contains(left: object, right: object) -> bool:
    # The exact scalar universe has only str as an iterable. Preserve the
    # existing host operation's TypeError for every other scalar pair.
    if not isinstance(left, str) or not isinstance(right, str):
        raise TypeError("Invalid scalar containment operands")
    return left in right


def _not_contains(left: object, right: object) -> bool:
    return not _contains(left, right)


def _equal(left: object, right: object) -> bool:
    return left == right


def _not_equal(left: object, right: object) -> bool:
    return left != right


def _less(left: object, right: object) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left < right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left < right
    raise TypeError("Invalid scalar ordering operands")


def _less_equal(left: object, right: object) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left <= right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left <= right
    raise TypeError("Invalid scalar ordering operands")


def _greater(left: object, right: object) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left > right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left > right
    raise TypeError("Invalid scalar ordering operands")


def _greater_equal(left: object, right: object) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left >= right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left >= right
    raise TypeError("Invalid scalar ordering operands")


@dataclass(frozen=True, eq=False)
class Value:
    ints: IntLattice = field(default_factory=IntLattice.bottom)
    strings: StrLattice = field(default_factory=StrLattice.bottom)
    bools: frozenset[bool] = frozenset()
    floats: frozenset[float] = frozenset()
    none: bool = False
    refs: frozenset[ObjectId] = frozenset()
    top_sorts: frozenset[str] = frozenset()
    top: bool = False
    unbound: bool = False
    relations: frozenset[PartitionToken] = frozenset()

    def __post_init__(self) -> None:
        raw = tuple(self.floats)
        signed_zeros = {struct.pack("!d", value) for value in raw if value == 0.0}
        floating = frozenset(value for value in raw if math.isfinite(value))
        sorts = frozenset(self.top_sorts)
        if len(signed_zeros) > 1:
            floating = frozenset()
            sorts |= {"float"}
        if any(not math.isfinite(value) for value in raw):
            sorts |= {"float"}
        object.__setattr__(self, "floats", floating)
        object.__setattr__(self, "top_sorts", sorts)
        bools: frozenset[bool] = frozenset(self.bools)
        refs: frozenset[ObjectId] = frozenset(self.refs)
        relations: frozenset[PartitionToken] = frozenset(self.relations)
        object.__setattr__(self, "bools", bools)
        object.__setattr__(self, "refs", refs)
        object.__setattr__(self, "relations", relations)

    def _equality_key(self) -> ValueEqualityKey:
        return (
            self.ints,
            self.strings,
            self.bools,
            tuple(sorted(struct.pack("!d", value) for value in self.floats)),
            self.none,
            self.refs,
            self.top_sorts,
            self.top,
            self.unbound,
            self.relations,
        )

    @override
    def __eq__(self, other: object) -> bool:
        return type(other) is Value and self._equality_key() == other._equality_key()

    @override
    def __hash__(self) -> int:
        return hash(self._equality_key())

    @classmethod
    def bottom(cls) -> Value:
        return cls()

    @classmethod
    def unknown(cls) -> Value:
        return cls(top=True)

    @classmethod
    def ref(cls, identifier: ObjectId) -> Value:
        return cls(refs=frozenset((identifier,)))

    @classmethod
    def constant(cls, value: object) -> Value:
        if value is None:
            return cls(none=True)
        if type(value) is bool:
            return cls(bools=frozenset((value,)))
        if type(value) is int:
            return cls(ints=IntLattice.const(value))
        if type(value) is str:
            return cls(strings=StrLattice.const(value))
        if type(value) is float:
            return (
                cls(floats=frozenset((value,)))
                if math.isfinite(value)
                else cls(top_sorts=frozenset(("float",)))
            )
        return cls(top_sorts=frozenset((type(value).__name__,)))

    def is_bottom(self) -> bool:
        return (
            not (
                self.top
                or self.unbound
                or self.refs
                or self.bools
                or self.floats
                or self.none
                or self.top_sorts
            )
            and self.ints.is_bottom
            and self.strings.is_bottom()
        )

    def without_unbound(self) -> Value:
        return replace(self, unbound=False)

    def exact_constants(self, cap: int = 256) -> list[JsonScalar] | None:
        if self.top or self.refs or self.top_sorts or self.unbound:
            return None
        ints = frozenset() if self.ints.is_bottom else self.ints.get_consts()
        strs = frozenset() if self.strings.is_bottom() else self.strings.get_consts()
        if (
            ints is None
            and self.ints.lo is not None
            and self.ints.hi is not None
            and self.ints.hi - self.ints.lo < cap
        ):
            ints = frozenset(
                n for n in range(self.ints.lo, self.ints.hi + 1) if self.ints.may_contain(n)
            )
        if ints is None or strs is None:
            return None
        result: list[JsonScalar] = [
            *sorted(ints),
            *sorted(strs),
            *sorted(self.bools),
            *sorted(self.floats),
        ]
        if self.none:
            result.append(None)
        return result if len(result) <= cap else None

    def export(self, kinds: Mapping[ObjectId, str] | None = None) -> JsonObject:
        tagged: list[JsonValue] = []
        constants: list[JsonValue] = []
        integers = self.ints.get_consts()
        strings = self.strings.consts
        groups: list[tuple[str, list[JsonScalar] | None]] = [
            ("int", None if integers is None else list[JsonScalar](sorted(integers))),
            ("str", list[JsonScalar](sorted(strings))),
            ("bool", list[JsonScalar](sorted(self.bools))),
            ("float", list[JsonScalar](sorted(self.floats))),
        ]
        for kind, values in groups:
            if values is not None:
                for value in values:
                    tagged.append({"type": kind, "value": value})
                    constants.append(value)
        if self.none:
            tagged.append({"type": "none", "value": None})
            constants.append(None)
        types = set(self.top_sorts)
        if not self.ints.is_bottom:
            types.add("int")
        if not self.strings.is_bottom():
            types.add("str")
        if self.bools:
            types.add("bool")
        if self.floats:
            types.add("float")
        if self.none:
            types.add("none")
        if self.unbound:
            types.add("unbound")
        for ref in self.refs:
            types.add(kinds.get(ref, "object") if kinds else "object")
        out: JsonObject = {
            "constants": constants,
            "tagged_constants": tagged,
            "objects": list[JsonValue](sorted(self.refs)),
            "types": list[JsonValue](sorted(types)),
            "top": self.top,
            "top_sorts": list[JsonValue](sorted(self.top_sorts)),
            "may_unbound": self.unbound,
            "bottom": self.is_bottom(),
            "relations": KeyValueRelation(self.relations).export(),
        }
        if not self.ints.is_bottom and self.ints.get_consts() is None:
            out["integer_interval"] = {
                "lower": self.ints.lo,
                "upper": self.ints.hi,
                "parity": self.ints.parity.value,
            }
        if not self.strings.is_bottom() and self.strings.get_consts() is None:
            out["string_patterns"] = {
                "prefixes": list[JsonValue](sorted(self.strings.prefixes)),
                "suffixes": list[JsonValue](sorted(self.strings.suffixes)),
                "unknown": self.strings.has_unknown,
                "conjunctions": [[prefix, suffix] for prefix, suffix in self.strings.conjunctions],
            }
        return canonical_value_document(out)


def admit_value(value: Value, visit: Callable[[int], None] | None) -> None:
    if visit is None:
        return
    visit(1)
    for integer in value.ints.consts:
        visit(1 + integer.bit_length() // 8192)
    for bound in (value.ints.lo, value.ints.hi):
        if bound is not None:
            visit(1 + bound.bit_length() // 8192)
    for group in (value.strings.consts, value.strings.prefixes, value.strings.suffixes):
        for text in group:
            visit_json_text(text, visit)
    for prefix, suffix in value.strings.conjunctions:
        visit_json_text(prefix, visit)
        visit_json_text(suffix, visit)
    for group in (value.refs, value.top_sorts):
        for text in group:
            visit_json_text(text, visit)
    visit(len(value.bools) + len(value.floats))
    for relation in value.relations:
        visit(1)
        for text in (relation.point, relation.key_type, relation.key_repr, relation.handler):
            visit_json_text(text, visit)
        if relation.display_name is not None:
            visit_json_text(relation.display_name, visit)


@dataclass(frozen=True)
class ValueFacet:
    """One disjunct of Value's concretization, independent of enumeration caps."""

    kind: str
    value: Value


@dataclass(frozen=True)
class ValuePolicy:
    max_const_strings: int = 10
    max_const_ints: int = 10
    max_addr_set_size: int = 50
    max_scalar_pairs: int = 256
    max_int_bits: int = 4096


class ValueOperations:
    arena: IdentityArena
    policy: ValuePolicy
    integer: IntDomain
    string: StringDomain

    def __init__(
        self, policy: ValuePolicy | None = None, arena: IdentityArena | None = None
    ) -> None:
        self.arena = arena or IdentityArena()
        self.policy = policy or ValuePolicy()
        self.integer = IntDomain(max_consts=self.policy.max_const_ints)
        self.string = StringDomain(max_consts=self.policy.max_const_strings)

    def normalize(self, value: Value) -> Value:
        if not self.arena.aliases:
            return value
        return self.remap(value, self.arena.aliases)

    def join(self, a: Value, b: Value) -> Value:
        a = self.normalize(a)
        b = self.normalize(b)
        if a.is_bottom():
            return b
        if b.is_bottom():
            return a
        floats = a.floats | b.floats
        sorts = a.top_sorts | b.top_sorts
        zero_signs = {struct.pack("!d", value) for value in (*a.floats, *b.floats) if value == 0.0}
        # Python's frozenset equates the two zero signs.  Losing one would make
        # str/repr and division observations unsound, so use a float-only cover.
        if len(floats) > self.policy.max_const_ints or len(zero_signs) > 1:
            floats, sorts = frozenset(), sorts | {"float"}
        return Value(
            ints=self.integer.join(a.ints, b.ints),
            strings=self.string.join(a.strings, b.strings),
            bools=a.bools | b.bools,
            floats=floats,
            none=a.none or b.none,
            refs=a.refs | b.refs,
            top_sorts=frozenset(sorts),
            top=a.top or b.top,
            unbound=a.unbound or b.unbound,
            relations=a.relations | b.relations,
        )

    def join_all(self, values: Iterable[Value]) -> Value:
        return reduce(self.join, values, Value.bottom())

    def leq(self, a: Value, b: Value) -> bool:
        a = self.normalize(a)
        b = self.normalize(b)
        if b.top:
            return (not a.unbound or b.unbound) and a.refs <= b.refs and a.relations <= b.relations
        if a.top:
            return False
        return (
            ("int" in b.top_sorts or self.integer.leq(a.ints, b.ints))
            and ("str" in b.top_sorts or self.string.leq(a.strings, b.strings))
            and ("bool" in b.top_sorts or a.bools <= b.bools)
            and (
                "float" in b.top_sorts
                or {struct.pack("!d", x) for x in a.floats}
                <= {struct.pack("!d", x) for x in b.floats}
            )
            and (not a.none or b.none or "none" in b.top_sorts)
            and a.refs <= b.refs
            and a.top_sorts <= b.top_sorts
            and (not a.unbound or b.unbound)
            and a.relations <= b.relations
        )

    def widen(self, a: Value, b: Value) -> Value:
        joined = self.join(a, b)
        return replace(
            joined,
            ints=self.integer.widen(a.ints, b.ints),
            strings=self.string.widen(a.strings, b.strings),
        )

    def widen_input(self, a: Value, b: Value) -> Value:
        """Retain bounded finite integer inputs before interval widening.

        This input policy leaves the ordinary widening contract unchanged.
        Strict finite growth can add at most max_consts members before the
        original interval widening takes over; intervals never become finite.
        """
        joined = self.join(a, b)
        integers = joined.ints
        if not integers.is_enumerable() or len(integers.consts) > self.integer.max_consts:
            integers = self.integer.widen(a.ints, b.ints)
        return replace(
            joined,
            ints=integers,
            strings=self.string.widen(a.strings, b.strings),
        )

    def facets(self, value: Value) -> tuple[ValueFacet, ...]:
        """Split a union without enumerating or dropping any component.

        Bottom has no facets. Unknown and unbound are distinct from scalar
        sort tops; references remain protocol operands for the engine.
        Relations annotate values and do not add concrete scalar members.
        """
        result: list[ValueFacet] = []
        if not value.ints.is_bottom:
            result.append(ValueFacet("int", Value(ints=value.ints)))
        if not value.strings.is_bottom():
            result.append(ValueFacet("str", Value(strings=value.strings)))
        if value.bools:
            result.append(ValueFacet("bool", Value(bools=value.bools)))
        if value.floats:
            result.append(ValueFacet("float", Value(floats=value.floats)))
        if value.none:
            result.append(ValueFacet("none", Value(none=True)))
        for sort in sorted(value.top_sorts):
            if sort == "int":
                facet = Value(ints=IntLattice.top())
            elif sort == "str":
                facet = Value(strings=StrLattice.top())
            elif sort == "bool":
                facet = Value(bools=frozenset((False, True)))
            elif sort in {"none", "NoneType"}:
                facet = Value(none=True)
                sort = "none"
            else:
                facet = Value(top_sorts=frozenset((sort,)))
            result.append(ValueFacet(sort, facet))
        if value.refs:
            result.append(ValueFacet("reference", Value(refs=value.refs)))
        if value.top:
            result.append(ValueFacet("unknown", Value.unknown()))
        if value.unbound:
            result.append(ValueFacet("unbound", Value(unbound=True)))
        return tuple(result)

    def truth(self, value: Value) -> frozenset[bool]:
        result: set[bool] = set()
        for facet in self.facets(value):
            member = facet.value
            if facet.kind == "unbound":
                continue
            if facet.kind == "int":
                if member.ints.may_contain(0):
                    result.add(False)
                if not self.integer.leq(member.ints, IntLattice.const(0)):
                    result.add(True)
            elif facet.kind == "str":
                if member.strings.may_equal(""):
                    result.add(False)
                if not self.string.leq(member.strings, StrLattice.const("")):
                    result.add(True)
            elif facet.kind == "bool":
                result.update(member.bools)
            elif facet.kind == "none":
                result.add(False)
            elif facet.kind == "float" and not member.top_sorts:
                result.update(bool(item) for item in member.floats)
            else:
                result.update((False, True))
        return frozenset(result)

    def remap(self, value: Value, mapping: Mapping[ObjectId, ObjectId]) -> Value:
        from .reabstract import resolve_mapping

        return replace(
            value,
            refs=frozenset(resolve_mapping(r, mapping) for r in value.refs),
            relations=frozenset(
                replace(token, handler=resolve_mapping(token.handler, mapping))
                for token in value.relations
            ),
        )

    def refine(self, value: Value, op: ast.cmpop, constant: object, truth: bool = True) -> Value:
        if type(constant) is int and not value.top:
            methods: dict[type[ast.cmpop], Callable[[int], IntLattice]] = {
                ast.Eq: value.ints.refine_eq,
                ast.NotEq: value.ints.refine_neq,
                ast.Lt: value.ints.refine_lt,
                ast.LtE: value.ints.refine_le,
                ast.Gt: value.ints.refine_gt,
                ast.GtE: value.ints.refine_ge,
            }
            inverse: dict[type[ast.cmpop], type[ast.cmpop]] = {
                ast.Eq: ast.NotEq,
                ast.NotEq: ast.Eq,
                ast.Lt: ast.GtE,
                ast.LtE: ast.Gt,
                ast.Gt: ast.LtE,
                ast.GtE: ast.Lt,
            }
            cls = type(op) if truth else inverse.get(type(op))
            method = methods.get(cls) if cls is not None else None
            if method:
                return replace(value, ints=method(constant))
        constants = value.exact_constants(self.policy.max_scalar_pairs)
        funcs: dict[type[ast.AST], ScalarOperator] = {
            ast.Eq: _equal,
            ast.NotEq: _not_equal,
            ast.Is: operator.is_,
            ast.IsNot: operator.is_not,
            ast.Lt: _less,
            ast.LtE: _less_equal,
            ast.Gt: _greater,
            ast.GtE: _greater_equal,
        }
        func = funcs.get(type(op))
        if constants is not None and func:
            try:

                def allowed(x: JsonScalar) -> bool:
                    if isinstance(op, (ast.Is, ast.IsNot)):
                        answer, _ = self.scalar_binary(
                            op, Value.constant(x), Value.constant(constant)
                        )
                        return truth in answer.bools
                    return bool(func(x, constant)) == truth

                return self.join_all(Value.constant(x) for x in constants if allowed(x))
            except (TypeError, ValueError):
                pass
        return value

    def scalar_unary(self, op: ast.unaryop, value: Value) -> tuple[Value, set[str]]:
        """Scalar unary transfer; object protocols remain engine obligations."""
        if isinstance(op, ast.Not):
            truth_errors = {
                "unknown_truth_protocol"
                for facet in self.facets(value)
                if facet.kind not in {"int", "bool", "float", "str", "none", "complex", "unbound"}
            }
            if value.unbound:
                truth_errors.add("unbound_operand")
            return Value(bools=frozenset(not item for item in self.truth(value))), truth_errors
        values: list[Value] = []
        errors: set[str] = set()
        for facet in self.facets(value):
            if facet.kind == "unbound":
                errors.add("unbound_operand")
                continue
            if facet.kind not in {"int", "bool", "float", "str", "none", "complex"}:
                values.append(Value.unknown())
                errors.add("unknown_unary_operator")
                continue
            if facet.kind == "complex" and isinstance(op, (ast.UAdd, ast.USub)):
                values.append(Value(top_sorts=frozenset(("complex",))))
                continue
            if facet.kind not in {"int", "bool", "float"} or (
                isinstance(op, ast.Invert) and facet.kind == "float"
            ):
                errors.add("TypeError")
                continue
            if not isinstance(op, (ast.UAdd, ast.USub, ast.Invert)):
                values.append(Value.unknown())
                errors.add("unknown_unary_operator")
                continue
            constants = facet.value.exact_constants(self.policy.max_scalar_pairs)
            if constants is not None:
                for item in constants:
                    if not isinstance(item, (int, float)):
                        raise AssertionError("A numeric facet contains a nonnumeric scalar")
                    if isinstance(op, ast.UAdd):
                        answer = +item
                    elif isinstance(op, ast.USub):
                        answer = -item
                    elif isinstance(item, int):
                        answer = ~item
                    else:
                        raise AssertionError("A float reached integer inversion")
                    values.append(Value.constant(answer))
            elif facet.kind == "float":
                values.append(Value(top_sorts=frozenset(("float",))))
            else:
                values.append(Value(ints=IntLattice.top()))
        return self.join_all(values), errors

    def scalar_binary(
        self, op: ast.operator | ast.cmpop, left: Value, right: Value
    ) -> tuple[Value, set[str]]:
        """Join every facet pair; an enumeration cap only changes precision."""
        values: list[Value] = []
        errors: set[str] = set()
        for first in self.facets(left):
            for second in self.facets(right):
                value, pair_errors = self._scalar_pair(op, first, second)
                values.append(value)
                errors.update(pair_errors)
        return self.join_all(values), errors

    def _scalar_pair(
        self, op: ast.operator | ast.cmpop, first: ValueFacet, second: ValueFacet
    ) -> tuple[Value, set[str]]:
        left, right = first.value, second.value
        if "unbound" in {first.kind, second.kind}:
            return Value.bottom(), {"unbound_operand"}
        add_operation: ScalarOperator = operator.add
        subtract_operation: ScalarOperator = operator.sub
        multiply_operation: ScalarOperator = operator.mul
        divide_operation: ScalarOperator = operator.truediv
        floor_divide_operation: ScalarOperator = operator.floordiv
        modulo_operation: ScalarOperator = operator.mod
        power_operation: ScalarOperator = operator.pow
        left_shift_operation: ScalarOperator = operator.lshift
        right_shift_operation: ScalarOperator = operator.rshift
        bit_or_operation: ScalarOperator = operator.or_
        bit_and_operation: ScalarOperator = operator.and_
        bit_xor_operation: ScalarOperator = operator.xor
        funcs: dict[type[ast.AST], ScalarOperator] = {
            ast.Add: add_operation,
            ast.Sub: subtract_operation,
            ast.Mult: multiply_operation,
            ast.Div: divide_operation,
            ast.FloorDiv: floor_divide_operation,
            ast.Mod: modulo_operation,
            ast.Pow: power_operation,
            ast.LShift: left_shift_operation,
            ast.RShift: right_shift_operation,
            ast.BitOr: bit_or_operation,
            ast.BitAnd: bit_and_operation,
            ast.BitXor: bit_xor_operation,
            ast.Eq: _equal,
            ast.NotEq: _not_equal,
            ast.Lt: _less,
            ast.LtE: _less_equal,
            ast.Gt: _greater,
            ast.GtE: _greater_equal,
            ast.Is: operator.is_,
            ast.IsNot: operator.is_not,
            ast.In: _contains,
            ast.NotIn: _not_contains,
        }
        func = funcs.get(type(op))
        a = left.exact_constants(self.policy.max_scalar_pairs)
        b = right.exact_constants(self.policy.max_scalar_pairs)
        if (
            func
            and a is not None
            and b is not None
            and len(a) * len(b) <= self.policy.max_scalar_pairs
        ):
            values: list[Value] = []
            errors: set[str] = set()
            for x in a:
                for y in b:
                    try:
                        if (
                            isinstance(op, (ast.Pow, ast.LShift, ast.RShift))
                            and type(y) is int
                            and abs(y) > self.policy.max_int_bits
                        ):
                            covered, pair_errors = self._scalar_fallback(op, first, second)
                            values.append(covered)
                            errors.update(pair_errors)
                            continue
                        # Bound allocation before calling a host builtin operator.
                        repetition: tuple[str, int] | None = None
                        if type(x) is str and (type(y) is int or type(y) is bool):
                            repetition = (x, y)
                        elif type(y) is str and (type(x) is int or type(x) is bool):
                            repetition = (y, x)
                        if isinstance(op, ast.Mult) and repetition is not None:
                            text, count = repetition
                            if (
                                count < -sys.maxsize - 1
                                or count > sys.maxsize
                                or len(text) * count > sys.maxsize
                            ):
                                errors.add("OverflowError")
                                continue
                            if count <= 0:
                                values.append(Value.constant(""))
                                continue
                            if len(text) * count > self.policy.max_int_bits:
                                values.append(Value(strings=StrLattice.top()))
                                continue
                        if (
                            isinstance(op, ast.Add)
                            and type(x) is str
                            and type(y) is str
                            and len(x) + len(y) > self.policy.max_int_bits
                        ):
                            values.append(Value(strings=StrLattice.top()))
                            continue
                        if (
                            isinstance(op, ast.Pow)
                            and type(x) is int
                            and type(y) is int
                            and y >= 0
                            and x not in {-1, 0, 1}
                            and max(1, abs(x).bit_length()) * y > self.policy.max_int_bits
                        ):
                            values.append(Value(ints=IntLattice.top()))
                            continue
                        if (
                            isinstance(op, ast.LShift)
                            and type(x) is int
                            and type(y) is int
                            and y >= 0
                            and x.bit_length() + y > self.policy.max_int_bits
                        ):
                            values.append(Value(ints=IntLattice.top()))
                            continue
                        if isinstance(op, (ast.Is, ast.IsNot)):
                            if x is None or y is None or type(x) is bool or type(y) is bool:
                                identities = {x is y}
                            elif type(x) is not type(y) or x != y:
                                identities = {False}
                            else:
                                identities = {False, True}
                            values.append(
                                Value(
                                    bools=frozenset(
                                        not item if isinstance(op, ast.IsNot) else item
                                        for item in identities
                                    )
                                )
                            )
                            continue
                        result = func(x, y)
                        if type(result) is int and result.bit_length() > self.policy.max_int_bits:
                            values.append(Value(top_sorts=frozenset(("int",))))
                        elif type(result) is str and len(result) > self.policy.max_int_bits:
                            values.append(Value(strings=StrLattice.top()))
                        else:
                            values.append(Value.constant(result))
                    except (ArithmeticError, TypeError, ValueError, OverflowError) as exc:
                        errors.add(type(exc).__name__)
            return self.join_all(values), errors
        return self._scalar_fallback(op, first, second)

    def _scalar_fallback(
        self, op: ast.operator | ast.cmpop, left: ValueFacet, right: ValueFacet
    ) -> tuple[Value, set[str]]:
        """Sort-specific covers when finite host evaluation exceeds its budget."""
        kinds = {left.kind, right.kind}
        if isinstance(op, (ast.Is, ast.IsNot)):
            return Value(bools=frozenset((False, True))), set()
        if not kinds <= {"int", "bool", "float", "str", "none"}:
            return Value.unknown(), {"unknown_operator"}
        if isinstance(op, (ast.Eq, ast.NotEq)):
            return Value(bools=frozenset((False, True))), set()
        numeric = kinds <= {"int", "bool", "float"}
        integral = kinds <= {"int", "bool"}
        text = kinds == {"str"}
        if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
            if numeric or text:
                return Value(bools=frozenset((False, True))), set()
            return Value.bottom(), {"TypeError"}
        if isinstance(op, (ast.In, ast.NotIn)):
            if text:
                return Value(bools=frozenset((False, True))), set()
            return Value.bottom(), {"TypeError"}
        if isinstance(op, ast.Add) and text:
            return Value(strings=StrLattice.top()), set()
        if isinstance(op, ast.Mult) and (
            left.kind == "str"
            and right.kind in {"int", "bool"}
            or right.kind == "str"
            and left.kind in {"int", "bool"}
        ):
            return Value(strings=StrLattice.top()), {"OverflowError"}
        if isinstance(op, ast.Mod) and left.kind == "str":
            return Value(strings=StrLattice.top()), {"TypeError", "ValueError", "OverflowError"}
        if isinstance(op, (ast.BitOr, ast.BitAnd, ast.BitXor)) and integral:
            value = (
                Value(bools=frozenset((False, True)))
                if kinds == {"bool"}
                else Value(ints=IntLattice.top())
            )
            return value, set()
        if isinstance(op, (ast.LShift, ast.RShift)) and integral:
            return Value(ints=IntLattice.top()), {"ValueError", "OverflowError"}
        if numeric and isinstance(
            op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
        ):
            errors: set[str] = set()
            if "float" in kinds:
                errors.add("OverflowError")
            if isinstance(op, (ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)):
                errors.add("ZeroDivisionError")
            if isinstance(op, ast.Pow):
                errors.update(("OverflowError", "ValueError"))
                sorts = {"float", "complex"}
                if integral:
                    sorts.add("int")
                return Value(top_sorts=frozenset(sorts)), errors
            if isinstance(op, ast.Div) or "float" in kinds:
                return Value(top_sorts=frozenset(("float",))), errors
            return Value(ints=IntLattice.top()), errors
        if kinds <= {"int", "bool", "float", "str", "none"}:
            return Value.bottom(), {"TypeError"}
        return Value.unknown(), {"unknown_operator"}
