"""Engine-neutral reduced scalar/reference values with run-local operations.

Object and cell identities are opaque strings.  No pointer engine or singleton is
imported here.  Integer and string operations reuse the public scalar lattices.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from functools import reduce
from typing import Iterable
import ast
import operator
import math
from .ints import IntDomain, IntLattice, Parity
from .strings import StringDomain, StrLattice
from .relations import PartitionToken,KeyValueRelation


@dataclass(frozen=True)
class Value:
    ints: IntLattice = field(default_factory=IntLattice.bottom)
    strings: StrLattice = field(default_factory=StrLattice.bottom)
    bools: frozenset[bool] = frozenset()
    floats: frozenset[float] = frozenset()
    none: bool = False
    refs: frozenset[str] = frozenset()
    top_sorts: frozenset[str] = frozenset()
    top: bool = False
    unbound: bool = False
    relations: frozenset[PartitionToken] = frozenset()

    @classmethod
    def bottom(cls): return cls()
    @classmethod
    def unknown(cls): return cls(top=True)
    @classmethod
    def ref(cls, identifier): return cls(refs=frozenset((identifier,)))
    @classmethod
    def constant(cls, value):
        if value is None: return cls(none=True)
        if type(value) is bool: return cls(bools=frozenset((value,)))
        if type(value) is int: return cls(ints=IntLattice.const(value))
        if type(value) is str: return cls(strings=StrLattice.const(value))
        if type(value) is float: return cls(floats=frozenset((value,))) if math.isfinite(value) else cls(top_sorts=frozenset(("float",)))
        return cls(top_sorts=frozenset((type(value).__name__,)))

    def is_bottom(self):
        return not (self.top or self.unbound or self.refs or self.bools or self.floats or self.none or self.top_sorts) and self.ints.is_bottom and self.strings.is_bottom()

    def without_unbound(self): return replace(self, unbound=False)

    def exact_constants(self, cap=256):
        if self.top or self.refs or self.top_sorts or self.unbound: return None
        ints = frozenset() if self.ints.is_bottom else self.ints.get_consts()
        strs = frozenset() if self.strings.is_bottom() else self.strings.get_consts()
        if ints is None and self.ints.lo is not None and self.ints.hi is not None and self.ints.hi-self.ints.lo < cap:
            ints = frozenset(n for n in range(self.ints.lo,self.ints.hi+1) if self.ints.may_contain(n))
        if ints is None or strs is None: return None
        result = [*sorted(ints), *sorted(strs), *sorted(self.bools), *sorted(self.floats)]
        if self.none: result.append(None)
        return result if len(result) <= cap else None

    def export(self, kinds=None):
        tagged = []
        for kind, values in (("int", self.ints.get_consts()), ("str", self.strings.get_consts()), ("bool", self.bools), ("float", self.floats)):
            if values is not None:
                tagged.extend({"type": kind, "value": v} for v in sorted(values))
        if self.none: tagged.append({"type": "none", "value": None})
        types = set(self.top_sorts)
        if not self.ints.is_bottom: types.add("int")
        if not self.strings.is_bottom(): types.add("str")
        if self.bools: types.add("bool")
        if self.floats: types.add("float")
        if self.none: types.add("none")
        if self.unbound: types.add("unbound")
        for ref in self.refs: types.add(kinds.get(ref, "object") if kinds else "object")
        out = {"constants": [x["value"] for x in tagged], "tagged_constants": tagged,
               "objects": sorted(self.refs), "types": sorted(types), "top": self.top,
               "top_sorts": sorted(self.top_sorts), "may_unbound":self.unbound, "bottom": self.is_bottom(), "relations": KeyValueRelation(self.relations).export()}
        if not self.ints.is_bottom and self.ints.get_consts() is None:
            out["integer_interval"] = {"lower": self.ints.lo, "upper": self.ints.hi, "parity": self.ints.parity.value}
        if not self.strings.is_bottom() and self.strings.get_consts() is None:
            out["string_patterns"] = {"prefixes": sorted(self.strings.prefixes), "suffixes": sorted(self.strings.suffixes), "unknown": self.strings.has_unknown, "conjunctions": [list(x) for x in getattr(self.strings,"conjunctions",())]}
        return out


@dataclass(frozen=True)
class ValuePolicy:
    max_const_strings: int = 10
    max_const_ints: int = 10
    max_addr_set_size: int = 50
    max_scalar_pairs: int = 256
    max_int_bits: int = 4096


class ValueOperations:
    def __init__(self, policy=None):
        self.policy = policy or ValuePolicy()
        self.integer = IntDomain(max_consts=self.policy.max_const_ints)
        self.string = StringDomain(max_consts=self.policy.max_const_strings)

    def join(self, a: Value, b: Value):
        if a.is_bottom(): return b
        if b.is_bottom(): return a
        floats = a.floats | b.floats
        sorts = a.top_sorts | b.top_sorts
        if len(floats) > self.policy.max_const_ints: floats, sorts = frozenset(), sorts | {"float"}
        return Value(ints=self.integer.join(a.ints,b.ints), strings=self.string.join(a.strings,b.strings),
                     bools=a.bools | b.bools, floats=floats, none=a.none or b.none, refs=a.refs | b.refs,
                     top_sorts=frozenset(sorts), top=a.top or b.top, unbound=a.unbound or b.unbound,relations=a.relations|b.relations)

    def join_all(self, values: Iterable[Value]): return reduce(self.join, values, Value.bottom())

    def leq(self, a, b):
        if b.top: return (not a.unbound or b.unbound) and a.refs<=b.refs and a.relations<=b.relations
        if a.top: return False
        return (self.integer.leq(a.ints,b.ints) and self.string.leq(a.strings,b.strings)
                and a.bools <= b.bools and a.floats <= b.floats and (not a.none or b.none)
                and a.refs <= b.refs and a.top_sorts <= b.top_sorts and (not a.unbound or b.unbound) and a.relations<=b.relations)

    def widen(self, a, b):
        joined = self.join(a,b)
        return replace(joined, ints=self.integer.widen(a.ints,b.ints), strings=self.string.widen(a.strings,b.strings))

    def truth(self, value):
        if value.top or value.top_sorts or value.refs: return frozenset((False,True))
        constants=value.exact_constants(self.policy.max_scalar_pairs)
        if constants is not None: return frozenset(bool(x) for x in constants)
        result=set(value.bools)
        if value.none: result.add(False)
        if not value.ints.is_bottom:
            if value.ints.may_contain(0): result.add(False)
            if not self.integer.leq(value.ints, IntLattice.const(0)): result.add(True)
        if not value.strings.is_bottom():
            result.add(True)
            if value.strings.has_unknown or "" in value.strings.consts or "" in value.strings.prefixes or "" in value.strings.suffixes: result.add(False)
        return frozenset(result)

    def remap(self, value, mapping): return replace(value, refs=frozenset(mapping.get(r,r) for r in value.refs))

    def refine(self, value, op, constant, truth=True):
        if type(constant) is int and not value.top:
            methods={ast.Eq:"refine_eq",ast.NotEq:"refine_neq",ast.Lt:"refine_lt",ast.LtE:"refine_le",ast.Gt:"refine_gt",ast.GtE:"refine_ge"}
            inverse={ast.Eq:ast.NotEq,ast.NotEq:ast.Eq,ast.Lt:ast.GtE,ast.LtE:ast.Gt,ast.Gt:ast.LtE,ast.GtE:ast.Lt}
            cls=type(op) if truth else inverse.get(type(op))
            method=methods.get(cls)
            if method: return replace(value, ints=getattr(value.ints,method)(constant))
        constants=value.exact_constants(self.policy.max_scalar_pairs)
        funcs={ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Is:operator.is_,ast.IsNot:operator.is_not,ast.Lt:operator.lt,ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge}
        func=funcs.get(type(op))
        if constants is not None and func:
            try: return self.join_all(Value.constant(x) for x in constants if bool(func(x,constant))==truth)
            except (TypeError,ValueError): pass
        return value

    def scalar_binary(self, op, left, right):
        funcs={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,
               ast.FloorDiv:operator.floordiv,ast.Mod:operator.mod,ast.Pow:operator.pow,ast.LShift:operator.lshift,
               ast.RShift:operator.rshift,ast.BitOr:operator.or_,ast.BitAnd:operator.and_,ast.BitXor:operator.xor,
               ast.Eq:operator.eq,ast.NotEq:operator.ne,ast.Lt:operator.lt,ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge,
               ast.Is:operator.is_,ast.IsNot:operator.is_not,ast.In:lambda a,b:a in b,ast.NotIn:lambda a,b:a not in b}
        func=funcs.get(type(op)); a=left.exact_constants(self.policy.max_scalar_pairs); b=right.exact_constants(self.policy.max_scalar_pairs)
        if func and a is not None and b is not None and len(a)*len(b)<=self.policy.max_scalar_pairs:
            values=[]; errors=set()
            for x in a:
                for y in b:
                    try:
                        if isinstance(op,(ast.Pow,ast.LShift,ast.RShift)) and type(y) is int and abs(y)>self.policy.max_int_bits:
                            return Value(top_sorts=frozenset(("int",))), {"resource_integer"}
                        # Bound allocation before calling a host builtin operator.
                        if isinstance(op,ast.Mult) and ((type(x) is str and type(y) in {int,bool}) or (type(y) is str and type(x) in {int,bool})):
                            text,count=(x,y) if type(x) is str else (y,x)
                            if count<=0:values.append(Value.constant(""));continue
                            if len(text)*count>self.policy.max_int_bits:values.append(Value(strings=StrLattice.top()));continue
                        if isinstance(op,ast.Add) and type(x) is str and type(y) is str and len(x)+len(y)>self.policy.max_int_bits:
                            values.append(Value(strings=StrLattice.top()));continue
                        if isinstance(op,ast.Pow) and type(x) is int and type(y) is int and y>=0 and x not in {-1,0,1} and max(1,abs(x).bit_length())*y>self.policy.max_int_bits:
                            values.append(Value(ints=IntLattice.top()));continue
                        if isinstance(op,ast.LShift) and type(x) is int and type(y) is int and y>=0 and x.bit_length()+y>self.policy.max_int_bits:
                            values.append(Value(ints=IntLattice.top()));continue
                        result=func(x,y)
                        if type(result) is int and result.bit_length()>self.policy.max_int_bits: values.append(Value(top_sorts=frozenset(("int",))))
                        elif type(result) is str and len(result)>self.policy.max_int_bits: values.append(Value(strings=StrLattice.top()))
                        else: values.append(Value.constant(result))
                    except (ArithmeticError,TypeError,ValueError,OverflowError) as exc: errors.add(type(exc).__name__)
            return self.join_all(values),errors
        if isinstance(op,(ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.Is,ast.IsNot,ast.In,ast.NotIn)):
            return Value(bools=frozenset((False,True))),set()
        if isinstance(op,ast.Add) and not left.strings.is_bottom() and not right.strings.is_bottom():
            return Value(strings=StrLattice.top()),set()
        if not left.ints.is_bottom and not right.ints.is_bottom and isinstance(op,(ast.Add,ast.Sub,ast.Mult)):
            return Value(ints=IntLattice.top()),set()
        return Value.unknown(), {"unknown_operator"}
