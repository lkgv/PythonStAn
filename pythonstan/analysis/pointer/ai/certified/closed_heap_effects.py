"""Source-bound checks for finite, fully closed heap scenarios.

This is a partial operational checker with exact singleton states, not a general
framework summary and not a mechanized proof of Python semantics. It never
executes compiled target code or imports target modules. Reachable unsupported
operations and exhausted budgets prevent conditional verification. A completed
closed entry can independently check candidate final-global claims under the
explicit model contract; this result must not prune KCFA or inherit its claims.
"""
from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field
import hashlib
import math
import sys
from typing import Any

SEMANTICS_VERSION = "closed-heap-effects-v2"
CONTRACT = (
    "The only entry is execution of this exact source in an initially empty user namespace.",
    "Inputs, callbacks and mutable heap originate entirely in the modeled source; no later external calls are covered.",
    "Only explicitly modeled operations are admitted; imports other than future annotations, native code, concurrency, tracing, finalizers and resource failures are outside the model.",
    "Conditional results assume the implementation of the declared Python 3.12 subset; no mechanized correspondence proof is claimed.",
)
_EXCEPTIONS = {
    "BaseException": None, "Exception": "BaseException", "TypeError": "Exception",
    "ValueError": "Exception", "AttributeError": "Exception", "RuntimeError": "Exception",
    "NameError": "Exception", "UnboundLocalError": "NameError", "LookupError": "Exception",
    "KeyError": "LookupError", "IndexError": "LookupError", "AssertionError": "Exception",
    "StopIteration": "Exception", "ZeroDivisionError": "Exception",
    "GeneratorExit": "BaseException",
}


class Unsupported(Exception):
    pass


class BudgetExceeded(Exception):
    pass


@dataclass(frozen=True)
class Budget:
    max_steps: int = 100_000
    max_call_depth: int = 100
    max_objects: int = 10_000

    def __post_init__(self):
        for name in ("max_steps", "max_call_depth", "max_objects"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(name + " must be a positive integer")


@dataclass(eq=False)
class ImplicitModuleName:
    """Unknown ambient module label; no exact string fact is fabricated."""
    pass


@dataclass(eq=False)
class OpaqueObject:
    """Fresh ordinary object() identity with no extensible instance dictionary."""
    pass


@dataclass(eq=False)
class Seq:
    values: list
    mutable: bool = True


@dataclass(eq=False)
class Mapping:
    values: dict = field(default_factory=dict)
    readonly: bool = False


@dataclass(eq=False)
class View:
    receiver: Mapping
    kind: str


@dataclass(eq=False)
class RangeValue:
    values: range


@dataclass(eq=False)
class Class:
    name: str
    bases: tuple
    attrs: dict
    mro: tuple = ()
    metaclass: Any = None
    is_metaclass: bool = False
    exception_base: str | None = None
    qualname: str | None = None


@dataclass(eq=False)
class Instance:
    cls: Class
    attrs: dict = field(default_factory=dict)
    dict_view: Mapping | None = None


@dataclass(eq=False)
class Function:
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
    closure: Any
    defaults: tuple
    kw_defaults: dict
    local_names: frozenset
    mangle: str | None = None
    qualname: str = ""


@dataclass(eq=False)
class Bound:
    function: Any
    receiver: Any


@dataclass(eq=False)
class Descriptor:
    function: Any
    kind: str


@dataclass(frozen=True)
class Builtin:
    name: str


@dataclass(eq=False)
class Method:
    receiver: Any
    name: str


@dataclass(frozen=True)
class ExceptionValue:
    kind: str
    args: tuple = ()
    implicit: bool = False
    cls: Class | None = None


class Signal(Exception):
    def __init__(self, kind, value=None):
        self.kind, self.value = kind, value


@dataclass(eq=False)
class Iterator:
    receiver: Any
    kind: str
    index: int = 0
    keys: tuple | None = None
    exhausted: bool = False


_UNBOUND = object()
_MISSING = object()


def _mangle(name, class_name):
    if class_name and class_name.lstrip("_") and name.startswith("__") and not name.endswith("__"):
        return "_" + class_name.lstrip("_") + name
    return name


class Env:
    def __init__(self, parent=None, *, local_names=(), function=False, mangle=None, qualname=""):
        self.parent = parent
        self.values = {}
        self.local_names = frozenset(_mangle(n, mangle) for n in local_names)
        self.function = function
        self.mangle = mangle
        self.qualname = qualname
        self.current_exception = None

    def lookup(self, name):
        name = _mangle(name, self.mangle)
        if name in self.values:
            value = self.values[name]
            if value is _UNBOUND:
                raise Unsupported("cleared exception variable lookup")
            return value
        if self.function and name in self.local_names:
            raise Signal("raise", ExceptionValue("UnboundLocalError", (name,), implicit=True))
        if self.parent is not None:
            return self.parent.lookup(name)
        if name == "__builtins__":
            raise Unsupported("introspection of builtin environment")
        if name == "Ellipsis": return Ellipsis
        if name == "__debug__":
            return True
        if name.startswith("__") or name in {"Ellipsis", "NotImplemented"}:
            raise Unsupported("implicit global metadata or builtin singleton " + name)
        if hasattr(builtins, name):
            return Builtin(name)
        raise Signal("raise", ExceptionValue("NameError", (name,), implicit=True))

    def bind(self, name, value):
        self.values[_mangle(name, self.mangle)] = value


class _Locals(ast.NodeVisitor):
    def __init__(self):
        self.names = set()

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node):
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node):
        pass

    def visit_ListComp(self, node):
        pass

    visit_DictComp = visit_ListComp
    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_ExceptHandler(self, node):
        if node.name:
            self.names.add(node.name)
        for stmt in node.body:
            self.visit(stmt)


class _ScopeOperations(ast.NodeVisitor):
    def __init__(self): self.suspends=False; self.global_binding=False; self.annotations=False
    def visit_AnnAssign(self,node): self.annotations=True; self.generic_visit(node)
    def visit_Yield(self,node): self.suspends=True
    visit_YieldFrom=visit_Yield
    def visit_Global(self,node): self.global_binding=True
    visit_Nonlocal=visit_Global
    def visit_FunctionDef(self,node): pass
    visit_AsyncFunctionDef=visit_FunctionDef
    visit_ClassDef=visit_FunctionDef
    visit_Lambda=visit_FunctionDef


def _locals(node):
    visitor = _Locals()
    for stmt in node.body:
        visitor.visit(stmt)
    args = node.args
    visitor.names.update(a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs))
    visitor.names.update(a.arg for a in (args.vararg, args.kwarg) if a)
    return frozenset(visitor.names)


class ClosedHeapMachine:
    """An explicit AST state machine; values never contain host user callables."""

    def __init__(self, source: str, budget: Budget):
        if type(source) is not str or len(source.encode()) > 1_000_000:
            raise ValueError("source must be bounded text")
        self.source = source
        # Compilation checks syntax/scope legality only. The code object is
        # discarded and never evaluated; all target transitions use this AST.
        compile(source, "<closed-heap-model>", "exec", dont_inherit=True)
        self.tree = ast.parse(source)
        self.budget = budget
        self.steps = 0
        self.objects = 0
        self.depth = 0
        self.operations = set()
        self.calls = []
        self.active_exception = None
        self.heap_generators = []
        self.module_name = ImplicitModuleName()
        self.env = Env(qualname="<module>")
        self.future_annotations = any(
            isinstance(s, ast.ImportFrom) and s.module == "__future__"
            and any(a.name == "annotations" for a in s.names) for s in self.tree.body
        )

    def tick(self, operation):
        self.steps += 1
        self.operations.add(operation)
        if self.steps > self.budget.max_steps:
            raise BudgetExceeded("step budget exhausted")

    def allocate(self, value):
        self.objects += 1
        if self.objects > self.budget.max_objects:
            raise BudgetExceeded("object budget exhausted")
        return value

    def fail(self, kind, *args):
        raise Signal("raise", ExceptionValue(kind, args, implicit=True))

    def class_attr(self, cls, name):
        for parent in cls.mro:
            if name in parent.attrs:
                return parent.attrs[name]
        return _MISSING

    def type_method(self, instance, name):
        if not isinstance(instance, Instance):
            raise Unsupported("special method on non-modeled instance: " + name)
        result = self.class_attr(instance.cls, name)
        if result is _MISSING:
            self.fail("AttributeError", name)
        return self.bind_descriptor(result, instance, instance.cls)

    def is_data_descriptor(self, value):
        return isinstance(value, Instance) and any(
            self.class_attr(value.cls, name) is not _MISSING for name in ("__set__", "__delete__"))

    def bind_descriptor(self, value, instance, owner):
        if isinstance(value, Function):
            return value if instance is None else Bound(value, instance)
        if isinstance(value, Descriptor):
            return value.function if value.kind == "staticmethod" else Bound(value.function, owner)
        if isinstance(value, Instance):
            getter = self.class_attr(value.cls, "__get__")
            if getter is not _MISSING:
                if not isinstance(getter,Function):raise Unsupported("nonfunction descriptor protocol")
                return self.call(Bound(getter, value), [instance, owner], {})
        return value

    def getattr(self, value, name):
        from .heap_continuations import HeapGenerator
        from .heap_coroutines import Coroutine
        self.tick("attribute_read")
        if type(name) is not str:
            self.fail("TypeError", "attribute name must be string")
        if isinstance(value, HeapGenerator) and name in {"send", "throw", "close", "__next__", "__iter__"}: return Method(value,name)
        if isinstance(value, Coroutine) and name in {"send", "throw", "close"}: return Method(value,name)
        if isinstance(value, Instance):
            override = self.class_attr(value.cls, "__getattribute__")
            if override is not _MISSING:
                raise Unsupported("custom __getattribute__")
            try:
                field = self.class_attr(value.cls, name)
                if (field is not _MISSING and self.is_data_descriptor(field)
                        and self.class_attr(field.cls, "__get__") is not _MISSING):
                    return self.bind_descriptor(field, value, value.cls)
                if name == "__dict__" and field is _MISSING:
                    if value.dict_view is None:
                        value.dict_view = self.allocate(Mapping(value.attrs))
                    return value.dict_view
                if name == "__class__" and field is _MISSING:
                    return value.cls
                if name in value.attrs:
                    return value.attrs[name]
                if field is not _MISSING:
                    return self.bind_descriptor(field, value, value.cls)
                if name.startswith("__") and name not in {"__get__","__set__","__delete__","__set_name__","__isabstractmethod__"}:
                    raise Unsupported("implicit instance metadata " + name)
                self.fail("AttributeError", name)
            except Signal as signal:
                if signal.kind != "raise" or signal.value.kind != "AttributeError":
                    raise
                fallback = self.class_attr(value.cls, "__getattr__")
                if fallback is _MISSING:
                    raise
                if not isinstance(fallback,Function):raise Unsupported("nonfunction attribute fallback protocol")
                return self.call(Bound(fallback, value), [name], {})
        if isinstance(value, Class):
            return self.class_getattr(value, name)
        if isinstance(value, OpaqueObject):
            if name == "__class__": return Builtin("object")
            if name in {"__dict__", "__get__", "__set__", "__delete__", "__set_name__"}: self.fail("AttributeError", name)
            raise Unsupported("opaque object attribute " + name)
        if isinstance(value, Descriptor) and name == "__get__":
            return Method(value, name)
        if isinstance(value, Descriptor) and name == "__set_name__":
            self.fail("AttributeError", name)
        if isinstance(value, Function) and name == "__call__": return Method(value,name)
        if isinstance(value, Function) and name == "__name__":
            return value.node.name
        if isinstance(value, Builtin) and name == "__name__":
            return value.name
        if isinstance(value,ExceptionValue) and value.cls is not None:
            if self.class_attr(value.cls,"__getattribute__") is not _MISSING or self.class_attr(value.cls,"__getattr__") is not _MISSING:
                raise Unsupported("custom exception attribute access")
            override=self.class_attr(value.cls,name)
            if override is not _MISSING:return self.bind_descriptor(override,value,value.cls)
        if isinstance(value, ExceptionValue) and name == "value" and value.kind == "StopIteration":
            return value.args[0] if value.args else None
        if isinstance(value, Builtin) and value.name == "type" and name in {"__new__", "__init__", "__prepare__"}:
            return Method(value, name)
        if isinstance(value, ExceptionValue) and name == "args":
            if value.implicit:
                raise Unsupported("implementation-specific implicit exception arguments")
            return self.allocate(Seq(list(value.args), False))
        if isinstance(value, Seq) and value.mutable and name in {"append", "extend", "pop", "reverse", "copy"}:
            return Method(value, name)
        if isinstance(value, Mapping) and name in {"get", "items", "keys", "values", "update", "copy", "setdefault"}:
            return Method(value, name)
        if type(value) is str and name in {"startswith", "endswith", "join", "upper", "lower", "replace"}:
            return Method(value, name)
        if isinstance(value, Instance):
            self.fail("AttributeError", name)
        if isinstance(value, Function) and not name.startswith("__"):
            self.fail("AttributeError", name)
        if isinstance(value, (Builtin, Function)):
            raise Unsupported("unmodeled callable attribute " + name)
        raise Unsupported("unmodeled builtin or primitive attribute " + name)

    def setattr(self, obj, name, value):
        self.tick("attribute_write")
        if type(name) is not str:
            self.fail("TypeError", "attribute name must be string")
        if isinstance(obj, Instance):
            if self.class_attr(obj.cls, "__setattr__") is not _MISSING:
                raise Unsupported("custom __setattr__")
            if name in {"__dict__", "__class__"}:
                raise Unsupported("replacement of instance layout or dictionary")
            descriptor = self.class_attr(obj.cls, name)
            if self.is_data_descriptor(descriptor):
                setter = self.class_attr(descriptor.cls, "__set__")
                if setter is _MISSING:
                    self.fail("AttributeError", name)
                if not isinstance(setter,Function):raise Unsupported("nonfunction descriptor setter protocol")
                self.call(Bound(setter, descriptor), [obj, value], {})
            else:
                obj.attrs[name] = value
            return
        if isinstance(obj, Class):
            meta = obj.metaclass
            if meta is not None and self.class_attr(meta,"__setattr__") is not _MISSING:
                raise Unsupported("custom metaclass __setattr__")
            descriptor = self.class_attr(meta,name) if meta is not None else _MISSING
            if self.is_data_descriptor(descriptor):
                setter = self.class_attr(descriptor.cls,"__set__")
                if setter is _MISSING: self.fail("AttributeError",name)
                if not isinstance(setter,Function): raise Unsupported("nonfunction metaclass setter")
                self.call(Bound(setter,descriptor),[obj,value],{})
                return
            if name.startswith("__") and name.endswith("__"):
                raise Unsupported("mutation of class protocol")
            obj.attrs[name] = value
            return
        raise Unsupported("attribute writes on external or primitive values")

    def truth(self, value):
        from .heap_continuations import HeapGenerator
        from .heap_coroutines import Coroutine
        if isinstance(value,(HeapGenerator,Coroutine)): return True
        if isinstance(value, (Seq, Mapping, RangeValue)):
            return bool(value.values)
        if isinstance(value, View):
            return bool(value.receiver.values)
        protocol_class = value.cls if isinstance(value,(Instance,ExceptionValue)) else value.metaclass if isinstance(value,Class) else None
        if protocol_class is not None:
            for name in ("__bool__", "__len__"):
                method = self.class_attr(protocol_class, name)
                if method is not _MISSING:
                    if not isinstance(method,Function):raise Unsupported("nonfunction truth protocol")
                    answer = self.call(Bound(method, value), [], {})
                    if name == "__bool__" and type(answer) is not bool:
                        self.fail("TypeError", "__bool__ must return bool")
                    if name == "__len__" and type(answer) not in (int,bool):
                        self.fail("TypeError", "__len__ must return int")
                    if name == "__len__" and answer < 0:
                        self.fail("ValueError", "negative length")
                    if name == "__len__" and answer > sys.maxsize:
                        raise Unsupported("platform length overflow")
                    return bool(answer)
            return True
        if value is Ellipsis: return True
        if value is None or type(value) in (bool, int, float, str):
            return bool(value)
        if isinstance(value, (OpaqueObject, Class, Function, Bound, Descriptor, Builtin, Method, ExceptionValue, Iterator, View, RangeValue)):
            return True
        raise Unsupported("unknown truth value")

    def key(self, value):
        if value is None or type(value) in (bool, int, float, str):
            return value
        if isinstance(value, Seq) and not value.mutable:
            return tuple(self.key(v) for v in value.values)
        if isinstance(value,Class) and value.metaclass is not None and any(self.class_attr(value.metaclass,n) is not _MISSING for n in ("__hash__","__eq__")):
            raise Unsupported("custom metaclass hash/equality")
        if isinstance(value,Instance):
            if any(self.class_attr(value.cls,n) is not _MISSING for n in ("__hash__","__eq__")):
                raise Unsupported("custom instance hash/equality")
            return value
        if isinstance(value, (OpaqueObject, Function, Class)):
            return value
        raise Unsupported("dictionary key may invoke custom hashing/equality")

    def compare(self, op, left, right):
        if isinstance(left,ImplicitModuleName) or isinstance(right,ImplicitModuleName): raise Unsupported("ambient module name comparison")
        if isinstance(op, (ast.Is, ast.IsNot)):
            if isinstance(left,Seq) and isinstance(right,Seq) and not left.mutable and not right.mutable and left is not right:
                raise Unsupported("immutable tuple pooling identity")
            # Heap references preserve allocation identity. Identity of immutable
            # scalar values beyond None/bool is implementation dependent.
            if (right is not None and type(right) not in (bool,) and left is not None and type(left) not in (bool,)
                    and not isinstance(left, (OpaqueObject, Seq, Mapping, Class, Instance, Function, Bound, Descriptor, Builtin, Method, ExceptionValue, Iterator))):
                raise Unsupported("identity comparison on scalar allocation")
            result = left is right or isinstance(left, Builtin) and left == right
            return not result if isinstance(op, ast.IsNot) else result
        if isinstance(op, (ast.In, ast.NotIn)):
            if isinstance(right, Mapping):
                result = self.key(left) in right.values
            elif isinstance(right, Seq):
                result = any(self.compare(ast.Eq(), left, item) for item in right.values)
            elif type(right) is str and type(left) is str:
                result = left in right
            else:
                raise Unsupported("custom membership protocol")
            return not result if isinstance(op, ast.NotIn) else result
        if isinstance(left, OpaqueObject) or isinstance(right, OpaqueObject):
            if isinstance(left, (Instance, Class)) or isinstance(right, (Instance, Class)) or isinstance(left,ExceptionValue) and left.cls is not None or isinstance(right,ExceptionValue) and right.cls is not None:
                raise Unsupported("opaque/custom equality reflection")
            if isinstance(op, ast.Eq): return left is right
            if isinstance(op, ast.NotEq): return left is not right
            self.fail("TypeError", "opaque objects cannot be ordered")
        if isinstance(left, (Instance, Class, Bound, Descriptor, Method, View, ExceptionValue)) or isinstance(right, (Instance, Class, Bound, Descriptor, Method, View, ExceptionValue)):
            raise Unsupported("custom comparison protocol")
        if isinstance(left, RangeValue) or isinstance(right, RangeValue):
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                raise Unsupported("range ordering")
            eq = isinstance(left, RangeValue) and isinstance(right, RangeValue) and left.values == right.values
            return not eq if isinstance(op, ast.NotEq) else eq
        if isinstance(left, Seq) or isinstance(right, Seq):
            if not isinstance(left, Seq) or not isinstance(right, Seq) or left.mutable != right.mutable:
                eq = False
            else:
                eq = len(left.values) == len(right.values) and all(self.compare(ast.Eq(), a, b) for a, b in zip(left.values, right.values))
            if isinstance(op, ast.Eq): return eq
            if isinstance(op, ast.NotEq): return not eq
            raise Unsupported("sequence ordering")
        if isinstance(left, Mapping) or isinstance(right, Mapping):
            raise Unsupported("mapping comparison")
        try:
            if isinstance(op, ast.Eq): return left == right
            if isinstance(op, ast.NotEq): return left != right
            if isinstance(op, ast.Lt): return left < right
            if isinstance(op, ast.LtE): return left <= right
            if isinstance(op, ast.Gt): return left > right
            if isinstance(op, ast.GtE): return left >= right
        except TypeError:
            self.fail("TypeError", "incomparable values")
        raise Unsupported("comparison operator")

    def iterate(self, value):
        from .heap_continuations import HeapGenerator
        if isinstance(value,HeapGenerator): return value
        if isinstance(value, Iterator): return value
        if isinstance(value, Seq): return self.allocate(Iterator(value, "seq"))
        if isinstance(value, Mapping): return self.allocate(Iterator(value, "keys", keys=tuple(value.values)))
        if isinstance(value, View): return self.allocate(Iterator(value.receiver,value.kind,keys=tuple(value.receiver.values)))
        if isinstance(value, RangeValue): return self.allocate(Iterator(value,"range"))
        if type(value) is str: return self.allocate(Iterator(value, "string"))
        raise Unsupported("custom iteration protocol")

    def next_value(self, it):
        from .heap_continuations import HeapGenerator,resume_heap_generator
        if isinstance(it,HeapGenerator): return resume_heap_generator(self,it,"next")
        self.tick("iteration_step")
        if it.exhausted:
            self.fail("StopIteration")
        if it.kind == "reversed_seq":
            if it.index < 0 or it.index >= len(it.receiver.values):
                it.exhausted = True
                self.fail("StopIteration")
            result = it.receiver.values[it.index]
            it.index -= 1
            return result
        if it.kind in {"keys", "values", "items"}:
            if tuple(it.receiver.values) != it.keys:
                raise Unsupported("mapping key mutation during iteration")
            if it.index >= len(it.keys):
                it.exhausted = True
                self.fail("StopIteration")
            key = it.keys[it.index]
            result = key if it.kind == "keys" else it.receiver.values[key]
            if it.kind == "items": result = self.allocate(Seq([key, result], False))
        else:
            values = it.receiver.values if isinstance(it.receiver, (Seq,RangeValue)) else it.receiver
            if it.index >= len(values):
                it.exhausted = True
                self.fail("StopIteration")
            result = values[it.index]
        it.index += 1
        return result

    def elements(self, value):
        iterator = self.iterate(value)
        while True:
            try: yield self.next_value(iterator)
            except Signal as signal:
                if signal.kind == "raise" and signal.value.kind == "StopIteration": return
                raise

    def assign(self, node, value, env):
        if isinstance(node, ast.Name):
            env.bind(node.id, value)
        elif isinstance(node, (ast.Tuple, ast.List)):
            values = list(self.elements(value))
            starred = [i for i, n in enumerate(node.elts) if isinstance(n, ast.Starred)]
            if not starred:
                if len(values) != len(node.elts): self.fail("ValueError", "unpacking arity")
                pairs = zip(node.elts, values)
            else:
                if len(starred) != 1: raise Unsupported("multiple starred targets")
                i = starred[0]; suffix = len(node.elts) - i - 1
                if len(values) < len(node.elts)-1: self.fail("ValueError", "unpacking arity")
                middle = values[i:len(values)-suffix if suffix else len(values)]
                pairs = zip(node.elts, values[:i]+[self.allocate(Seq(middle))]+(values[-suffix:] if suffix else []))
            for target, item in pairs:
                self.assign(target.value if isinstance(target, ast.Starred) else target, item, env)
        elif isinstance(node, ast.Attribute):
            self.setattr(self.expr(node.value, env), _mangle(node.attr, env.mangle), value)
        elif isinstance(node, ast.Subscript):
            obj, key = self.expr(node.value, env), self.expr(node.slice, env)
            if isinstance(obj, Mapping):
                if obj.readonly: self.fail("TypeError", "readonly mapping")
                obj.values[self.key(key)] = value
            elif isinstance(obj, Seq) and obj.mutable and type(key) is int:
                try: obj.values[key] = value
                except IndexError: self.fail("IndexError", key)
            else: raise Unsupported("custom subscript assignment")
        else: raise Unsupported("assignment target " + type(node).__name__)

    def expr(self, node, env):
        self.tick(type(node).__name__)
        if isinstance(node,ast.Await):
            from .heap_coroutines import await_value
            return await_value(self,self.expr(node.value,env))
        if isinstance(node, ast.Constant):
            if node.value is None or node.value is Ellipsis or type(node.value) in (bool, int, float, str): return node.value
            raise Unsupported("constant type")
        if isinstance(node, ast.Name): return env.lookup(node.id)
        if isinstance(node, ast.Attribute): return self.getattr(self.expr(node.value, env), _mangle(node.attr, env.mangle))
        if isinstance(node, (ast.List, ast.Tuple)):
            values = []
            for item in node.elts:
                if isinstance(item, ast.Starred): values.extend(self.elements(self.expr(item.value, env)))
                else: values.append(self.expr(item, env))
            return self.allocate(Seq(values, isinstance(node, ast.List)))
        if isinstance(node, ast.Dict):
            result = self.allocate(Mapping())
            for k, v in zip(node.keys, node.values):
                if k is None:
                    other = self.expr(v, env)
                    if not isinstance(other, Mapping): raise Unsupported("mapping unpack protocol")
                    result.values.update(other.values)
                else:
                    key = self.key(self.expr(k, env)); result.values[key] = self.expr(v, env)
            return result
        if isinstance(node, ast.Call):
            callee = self.expr(node.func, env)
            args, kwargs = [], {}
            for arg in node.args:
                if isinstance(arg, ast.Starred): args.extend(self.elements(self.expr(arg.value, env)))
                else: args.append(self.expr(arg, env))
            for keyword in node.keywords:
                value = self.expr(keyword.value, env)
                additions = value.values if keyword.arg is None and isinstance(value, Mapping) else {keyword.arg: value}
                if keyword.arg is None and not isinstance(value, Mapping): raise Unsupported("custom keyword mapping")
                for name, val in additions.items():
                    if type(name) is not str: self.fail("TypeError", "keyword must be string")
                    if name in kwargs: self.fail("TypeError", "duplicate keyword")
                    kwargs[name] = val
            return self.call(callee, args, kwargs)
        if isinstance(node, ast.Compare):
            left = self.expr(node.left, env)
            for op, rhs in zip(node.ops, node.comparators):
                right = self.expr(rhs, env)
                if not self.compare(op, left, right): return False
                left = right
            return True
        if isinstance(node, ast.BoolOp):
            for index, item in enumerate(node.values):
                value = self.expr(item, env)
                if index == len(node.values)-1 or self.truth(value) == isinstance(node.op, ast.Or): return value
        if isinstance(node, ast.UnaryOp):
            value = self.expr(node.operand, env)
            if isinstance(node.op, ast.Not): return not self.truth(value)
            if type(value) not in (int, float, bool): raise Unsupported("custom unary operation")
            if isinstance(node.op, ast.USub): return -value
            if isinstance(node.op, ast.UAdd): return +value
            if isinstance(node.op, ast.Invert) and type(value) in (int, bool): return ~value
            raise Unsupported("unary operator")
        if isinstance(node, ast.BinOp):
            left, right = self.expr(node.left, env), self.expr(node.right, env)
            if isinstance(node.op, ast.Add) and isinstance(left, Seq) and isinstance(right, Seq) and left.mutable == right.mutable:
                if len(left.values)+len(right.values)>self.budget.max_steps:raise BudgetExceeded("sequence allocation budget")
                return self.allocate(Seq(left.values+right.values, left.mutable))
            if type(left) not in (int, float, bool, str) or type(right) not in (int, float, bool, str): raise Unsupported("custom binary operation")
            if isinstance(node.op, ast.Mod) and type(left) is str:raise Unsupported("percent string formatting")
            if isinstance(node.op, ast.Mult):
                scalar, count = (left, right) if type(left) is str else (right, left)
                if type(scalar) is str and type(count) in (int,bool) and len(scalar)*max(count,0)>1_000_000:raise BudgetExceeded("string allocation budget")
                if type(left) in (int,bool) and type(right) in (int,bool) and left.bit_length()+right.bit_length()>100_000:raise BudgetExceeded("integer allocation budget")
            if isinstance(node.op, ast.Add) and type(left) is str and type(right) is str and len(left)+len(right)>1_000_000:raise BudgetExceeded("string allocation budget")
            try:
                if isinstance(node.op, ast.Add): return left + right
                if isinstance(node.op, ast.Sub): return left - right
                if isinstance(node.op, ast.Mult): return left * right
                if isinstance(node.op, ast.Div): return left / right
                if isinstance(node.op, ast.FloorDiv): return left // right
                if isinstance(node.op, ast.Mod): return left % right
            except TypeError: self.fail("TypeError", "binary operands")
            except ZeroDivisionError: self.fail("ZeroDivisionError")
            raise Unsupported("binary operator")
        if isinstance(node, ast.IfExp): return self.expr(node.body if self.truth(self.expr(node.test, env)) else node.orelse, env)
        if isinstance(node, ast.Subscript):
            obj, key = self.expr(node.value, env), self.expr(node.slice, env)
            if isinstance(obj, Mapping):
                key = self.key(key)
                if key not in obj.values: self.fail("KeyError", key)
                return obj.values[key]
            if isinstance(obj, Seq) or type(obj) is str:
                if type(key) is not int: raise Unsupported("noninteger sequence index")
                try: return obj.values[key] if isinstance(obj, Seq) else obj[key]
                except IndexError: self.fail("IndexError", key)
            if isinstance(obj, Instance):
                return self.call(self.type_method(obj, "__getitem__"), [key], {})
            raise Unsupported("custom subscripting")
        if isinstance(node, (ast.ListComp, ast.DictComp)):
            targets = {n.id for generator in node.generators for n in ast.walk(generator.target) if isinstance(n, ast.Name)}
            local = Env(env.parent if getattr(env,"class_body",False) else env, local_names=targets, function=True, mangle=env.mangle, qualname=env.qualname+".<comprehension>")
            result = self.allocate(Mapping() if isinstance(node, ast.DictComp) else Seq([]))
            def walk(index):
                if index == len(node.generators):
                    if isinstance(node, ast.DictComp):
                        key = self.key(self.expr(node.key, local)); result.values[key] = self.expr(node.value, local)
                    else: result.values.append(self.expr(node.elt, local))
                    return
                generator = node.generators[index]
                if generator.is_async: raise Unsupported("async comprehension")
                iterable_env = env if index == 0 else local
                for value in self.elements(self.expr(generator.iter, iterable_env)):
                    self.assign(generator.target, value, local)
                    if all(self.truth(self.expr(cond, local)) for cond in generator.ifs): walk(index+1)
            walk(0)
            return result
        if isinstance(node, ast.JoinedStr):
            parts=[]
            for part in node.values:
                if isinstance(part, ast.Constant): parts.append(part.value); continue
                value=self.expr(part.value,env)
                if value is not None and type(value) not in (int,float,bool,str): raise Unsupported("custom string formatting")
                text=repr(value) if part.conversion==114 else str(value)
                if part.format_spec is not None: raise Unsupported("format specifiers")
                if part.conversion not in (-1,115,114): raise Unsupported("format conversion")
                parts.append(text)
            return "".join(parts)
        raise Unsupported("expression " + type(node).__name__)

    def function(self, node, env):
        if not self.future_annotations and (node.returns is not None or any(a.annotation for a in (*node.args.posonlyargs,*node.args.args,*node.args.kwonlyargs)) or any(a and a.annotation for a in (node.args.vararg,node.args.kwarg))):
            raise Unsupported("evaluated annotations")
        decorators = [self.expr(item, env) for item in node.decorator_list]
        defaults = tuple(self.expr(item, env) for item in node.args.defaults)
        kw_defaults = {_mangle(arg.arg,env.mangle):self.expr(value,env) for arg,value in zip(node.args.kwonlyargs,node.args.kw_defaults) if value is not None}
        closure = env.parent if getattr(env,"class_body",False) else env
        name = env.qualname + (".<locals>." if env.function else ".") + node.name if env.qualname != "<module>" else node.name
        value = self.allocate(Function(node,closure,defaults,kw_defaults,_locals(node),env.mangle,name))
        for decorator in reversed(decorators): value=self.call(decorator,[value],{})
        env.bind(node.name,value)

    def class_getattr(self, cls, name):
        try:return self._class_getattr(cls,name)
        except Signal as signal:
            if signal.kind!="raise" or signal.value.kind!="AttributeError":raise
            meta=cls.metaclass
            hook=self.class_attr(meta,"__getattr__") if meta is not None else _MISSING
            if hook is _MISSING:raise
            if not isinstance(hook,Function):raise Unsupported("nonfunction metaclass fallback")
            return self.call(Bound(hook,cls),[name],{})

    def _class_getattr(self, cls, name):
        meta = cls.metaclass
        if meta is not None and self.class_attr(meta, "__getattribute__") is not _MISSING:
            raise Unsupported("custom metaclass __getattribute__")
        meta_field = self.class_attr(meta, name) if meta is not None else _MISSING
        if meta_field is not _MISSING and self.is_data_descriptor(meta_field) and self.class_attr(meta_field.cls, "__get__") is not _MISSING:
            return self.bind_descriptor(meta_field, cls, meta)
        if meta_field is not _MISSING and name in {"__class__","__dict__","__mro__","__bases__"}:
            raise Unsupported("metaclass overrides implicit layout descriptor")
        if meta_field is _MISSING:
            if name == "__name__": return cls.name
            if name == "__qualname__": return cls.qualname or cls.name
            if name == "__class__": return meta or Builtin("type")
            if name == "__dict__": raise Unsupported("class namespace proxy")
        field = self.class_attr(cls, name)
        if field is not _MISSING: return self.bind_descriptor(field, None, cls)
        if meta_field is not _MISSING: return self.bind_descriptor(meta_field, cls, meta)
        if cls.is_metaclass and name in {"__new__", "__init__", "__prepare__"}:
            return Method(Builtin("type"), name)
        if name.startswith("__"): raise Unsupported("implicit class metadata " + name)
        self.fail("AttributeError", name)

    def select_metaclass(self, explicit, bases):
        if explicit == Builtin("type"): explicit = None
        if explicit is not None and (not isinstance(explicit, Class) or not explicit.is_metaclass):
            raise Unsupported("non-type metaclass callable")
        selected = explicit
        for base in bases:
            if not isinstance(base, Class): continue
            candidate = base.metaclass
            if candidate is None: continue
            if selected is None or selected in candidate.mro: selected = candidate
            elif candidate not in selected.mro: self.fail("TypeError", "metaclass conflict")
        return selected

    def new_class(self, meta, name, bases, namespace, kwargs=None):
        if type(name) is not str or not isinstance(bases, Seq) or bases.mutable or not isinstance(namespace, Mapping):
            self.fail("TypeError", "type.__new__ signature")
        if meta is not None and (not isinstance(meta, Class) or not meta.is_metaclass):
            self.fail("TypeError", "type.__new__ requires type subtype")
        if "\x00" in name: self.fail("ValueError","type name must not contain null characters")
        raw_bases = tuple(bases.values)
        if len(set(raw_bases)) != len(raw_bases): self.fail("TypeError", "duplicate base")
        native = [b for b in raw_bases if isinstance(b, Builtin)]
        user_bases = tuple(b for b in raw_bases if isinstance(b, Class))
        if len(native)+len(user_bases) != len(raw_bases): raise Unsupported("dynamic base adaptation")
        if any(b.name not in {"object", "type"} | set(_EXCEPTIONS) for b in native): raise Unsupported("native base class")
        if native and (len(native) != 1 or user_bases): raise Unsupported("mixed native base layout")
        if meta is not None and self.class_attr(meta,"mro") is not _MISSING:
            raise Unsupported("custom metaclass MRO")
        selected = self.select_metaclass(meta, user_bases)
        if selected is not meta: raise Unsupported("type.__new__ metaclass redirection")
        attrs = dict(namespace.values)
        attrs.setdefault("__doc__",None)
        qualname=attrs.pop("__qualname__",name)
        if type(qualname) is not str:self.fail("TypeError","class qualname must be string")
        if any(type(k) is not str for k in attrs): self.fail("TypeError", "class namespace keys")
        if any(k in attrs for k in ("__slots__", "__del__", "__classcell__")):
            raise Unsupported("class allocation/lifetime protocol")
        if any(self.class_attr(base,"__init_subclass__") is not _MISSING for base in user_bases):
            raise Unsupported("__init_subclass__ hook")
        if kwargs: raise Unsupported("class keyword subclass hook")
        is_meta = any(b.name == "type" for b in native) or any(b.is_metaclass for b in user_bases)
        exception_bases = [b.name for b in native if b.name in _EXCEPTIONS] + [b.exception_base for b in user_bases if b.exception_base]
        if len(set(exception_bases)) > 1: raise Unsupported("multiple exception base layouts")
        exception_base = exception_bases[0] if exception_bases else None
        if "__new__" in attrs and isinstance(attrs["__new__"], Function):
            attrs["__new__"] = self.allocate(Descriptor(attrs["__new__"], "staticmethod"))
        if "__init_subclass__" in attrs and isinstance(attrs["__init_subclass__"], Function):
            attrs["__init_subclass__"] = self.allocate(Descriptor(attrs["__init_subclass__"], "classmethod"))
        cls=self.allocate(Class(name,user_bases,attrs,metaclass=meta,is_metaclass=is_meta,exception_base=exception_base,qualname=qualname))
        chains=[list(base.mro) for base in user_bases]+[list(user_bases)];order=[]
        while any(chains):
            chosen=next((chain[0] for chain in chains if chain and all(chain[0] not in other[1:] for other in chains)),None)
            if chosen is None:self.fail("TypeError","inconsistent MRO")
            order.append(chosen)
            for chain in chains:
                if chain and chain[0] is chosen:chain.pop(0)
        cls.mro=(cls,*order)
        for key,value in tuple(cls.attrs.items()):
            if isinstance(value,Instance):
                hook=self.class_attr(value.cls,"__set_name__")
                if hook is not _MISSING:
                    if not isinstance(hook,Function):raise Unsupported("nonfunction set_name protocol")
                    try:self.call(Bound(hook,value),[cls,key],{})
                    except Signal:raise Unsupported("set_name construction failure and exception chaining")
        return cls

    def construct_class(self, meta, args, kwargs):
        if len(args) != 3: self.fail("TypeError", "class construction requires name, bases, namespace")
        new = self.class_attr(meta, "__new__") if meta is not None else _MISSING
        if new is _MISSING:
            cls = self.new_class(meta, *args, kwargs=kwargs)
        else:
            if isinstance(new, Descriptor) and new.kind == "staticmethod": new = new.function
            if not isinstance(new, Function): raise Unsupported("custom metaclass new descriptor")
            cls = self.call(new, [meta, *args], kwargs)
        # type.__call__ invokes __init__ only for a result that is an instance
        # of the requested metaclass; a custom __new__ may return another value.
        if isinstance(cls, Class) and (meta is None or cls.metaclass is not None and meta in cls.metaclass.mro):
            actual_meta = cls.metaclass
            init = self.class_attr(actual_meta, "__init__") if actual_meta is not None else _MISSING
            if init is not _MISSING:
                result = self.call(self.bind_descriptor(init, cls, actual_meta), args, kwargs)
                if result is not None: self.fail("TypeError", "metaclass __init__ must return None")
        return cls

    def define_class(self,node,env):
        if node.decorator_list: raise Unsupported("class decorators")
        bases=tuple(self.expr(base,env) for base in node.bases)
        keywords={}
        for keyword in node.keywords:
            if keyword.arg is None: raise Unsupported("class keyword unpacking")
            if keyword.arg in keywords: self.fail("TypeError", "duplicate class keyword")
            keywords[keyword.arg] = self.expr(keyword.value,env)
        meta = self.select_metaclass(keywords.pop("metaclass",None), bases)
        base_tuple = self.allocate(Seq(list(bases), False))
        prepare = self.class_getattr(meta, "__prepare__") if meta is not None else _MISSING
        if prepare is _MISSING: namespace = self.allocate(Mapping())
        else:
            namespace = self.call(prepare, [node.name,base_tuple], keywords)
            if not isinstance(namespace, Mapping) or namespace.readonly: raise Unsupported("custom class namespace protocol")
        qualname = env.qualname + (".<locals>." if env.function else ".") + node.name if env.qualname != "<module>" else node.name
        local=Env(env.parent if getattr(env,"class_body",False) else env,mangle=node.name,qualname=qualname);local.class_body=True
        local.values=namespace.values
        # These writes happen at class-body entry, after __prepare__. Omitting
        # the keys creates false absence/length facts in metaclass code.
        local.values["__module__"] = self.env.values.get("__name__",self.module_name)
        local.values["__qualname__"] = qualname
        scanner=_ScopeOperations()
        for statement in node.body:scanner.visit(statement)
        if scanner.annotations:local.values.setdefault("__annotations__",self.allocate(Mapping()))
        if node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and type(node.body[0].value.value) is str:
            local.values["__doc__"] = node.body[0].value.value
        self.block(node.body,local)
        # A reference requiring a __class__ cell has compiler-managed semantics
        # not represented by this environment. Do not drop that obligation.
        if any(isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load) and n.id in {"__class__", "super"} for n in ast.walk(node)):
            raise Unsupported("implicit class closure cell")
        cls = self.call(meta, [node.name,base_tuple,namespace], keywords) if meta is not None else self.construct_class(None, [node.name,base_tuple,namespace], keywords)
        env.bind(node.name,cls)

    def block(self,statements,env):
        for statement in statements:self.stmt(statement,env)

    def stmt(self,node,env):
        self.tick(type(node).__name__)
        if isinstance(node,ast.Expr):self.expr(node.value,env);return
        if isinstance(node,ast.Pass):return
        if isinstance(node,ast.ImportFrom) and node.module=="__future__" and all(a.name=="annotations" for a in node.names):return
        if isinstance(node,ast.Assign):
            value=self.expr(node.value,env)
            for target in node.targets:self.assign(target,value,env)
            return
        if isinstance(node,ast.AugAssign):
            # Evaluate a supported lvalue once before evaluating the RHS. The
            # writeback is still required after list in-place mutation.
            if isinstance(node.target, ast.Name):
                left=env.lookup(node.target.id)
                write=lambda value:env.bind(node.target.id,value)
            elif isinstance(node.target,ast.Attribute):
                owner=self.expr(node.target.value,env);name=_mangle(node.target.attr,env.mangle)
                left=self.getattr(owner,name)
                write=lambda value:self.setattr(owner,name,value)
            else:raise Unsupported("augmented subscript assignment")
            right=self.expr(node.value,env)
            if isinstance(node.op,ast.Add) and isinstance(left,Seq) and left.mutable:
                additions=list(right.values) if isinstance(right,Seq) else self.elements(right)
                for value in additions:
                    self.tick("list_inplace_extend")
                    if len(left.values)>=self.budget.max_steps:raise BudgetExceeded("list inplace allocation budget")
                    left.values.append(value)
                write(left);return
            raise Unsupported("custom/scalar augmented assignment")
        if isinstance(node,ast.AnnAssign):
            if not self.future_annotations:raise Unsupported("evaluated annotation")
            if node.value is not None:self.assign(node.target,self.expr(node.value,env),env)
            if isinstance(node.target,ast.Name) and node.simple and getattr(env,"class_body",False):
                annotations=env.values.get("__annotations__")
                if not isinstance(annotations,Mapping):raise Unsupported("custom class annotations mapping")
                annotations.values[_mangle(node.target.id,env.mangle)]=ast.unparse(node.annotation)
            return
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):self.function(node,env);return
        if isinstance(node,ast.ClassDef):self.define_class(node,env);return
        if isinstance(node,ast.Return):raise Signal("return",self.expr(node.value,env) if node.value else None)
        if isinstance(node,ast.If):self.block(node.body if self.truth(self.expr(node.test,env)) else node.orelse,env);return
        if isinstance(node,(ast.For,ast.While)):
            iterator=self.iterate(self.expr(node.iter,env)) if isinstance(node,ast.For) else None
            broke=False
            while True:
                self.tick("loop")
                if iterator is None:
                    if not self.truth(self.expr(node.test,env)):break
                else:
                    try:value=self.next_value(iterator)
                    except Signal as signal:
                        if signal.kind=="raise" and signal.value.kind=="StopIteration":break
                        raise
                    self.assign(node.target,value,env)
                try:self.block(node.body,env)
                except Signal as signal:
                    if signal.kind=="break":broke=True;break
                    if signal.kind!="continue":raise
            if not broke:self.block(node.orelse,env)
            return
        if isinstance(node,ast.Break):raise Signal("break")
        if isinstance(node,ast.Continue):raise Signal("continue")
        if isinstance(node,ast.Raise):
            if node.cause is not None:raise Unsupported("explicit exception cause")
            value=self.expr(node.exc,env) if node.exc else self.active_exception
            if isinstance(value,Builtin) and value.name in _EXCEPTIONS:value=ExceptionValue(value.name)
            if isinstance(value,Class) and value.exception_base:value=self.call(value,[],{})
            if not isinstance(value,ExceptionValue):
                self.fail("RuntimeError" if node.exc is None else "TypeError","invalid raise")
            raise Signal("raise",value)
        if isinstance(node,ast.Try):
            pending=None
            try:
                try:self.block(node.body,env)
                except Signal as signal:
                    if signal.kind!="raise":raise
                    previous=self.active_exception;self.active_exception=signal.value
                    try:
                        for handler in node.handlers:
                            if handler.type is None or self.exception_matches(signal.value,self.expr(handler.type,env)):
                                if handler.name:env.bind(handler.name,signal.value)
                                try:self.block(handler.body,env)
                                finally:
                                    if handler.name:env.bind(handler.name,_UNBOUND)
                                break
                        else:raise
                    finally:self.active_exception=previous
                else:self.block(node.orelse,env)
            except Signal as signal:pending=signal
            previous=self.active_exception
            if pending is not None and pending.kind=="raise":self.active_exception=pending.value
            try:self.block(node.finalbody,env)
            finally:self.active_exception=previous
            if pending:raise pending
            return
        if isinstance(node,ast.With):
            def enter(index):
                if index==len(node.items):self.block(node.body,env);return
                item=node.items[index];manager=self.expr(item.context_expr,env)
                begin=self.type_method(manager,"__enter__");end=self.type_method(manager,"__exit__")
                value=self.call(begin,[],{})
                try:
                    if item.optional_vars:self.assign(item.optional_vars,value,env)
                    enter(index+1)
                except Signal as signal:
                    if signal.kind=="raise":
                        # A true traceback cannot be observed by the admitted
                        # fragment. The token is deliberately noninspectable.
                        previous=self.active_exception;self.active_exception=signal.value
                        try:suppressed=self.call(end,[signal.value.cls or Builtin(signal.value.kind),signal.value,_Traceback()],{})
                        finally:self.active_exception=previous
                        if not self.truth(suppressed):raise
                    else:self.call(end,[None,None,None],{});raise
                else:self.call(end,[None,None,None],{})
            enter(0);return
        if isinstance(node,ast.Assert):
            if not self.truth(self.expr(node.test,env)):self.fail("AssertionError",self.expr(node.msg,env) if node.msg else "")
            return
        raise Unsupported("statement "+type(node).__name__)

    def exception_matches(self,value,kind):
        kinds = kind.values if isinstance(kind,Seq) and not kind.mutable else [kind]
        # CPython validates every entry before testing any match. Nested tuples
        # are invalid handler entries (unlike isinstance classinfo tuples).
        for item in kinds:
            if not (isinstance(item,Builtin) and item.name in _EXCEPTIONS or isinstance(item,Class) and item.exception_base):
                self.fail("TypeError","invalid exception handler")
        for item in kinds:
            if isinstance(item,Class):
                if value.cls is not None and item in value.cls.mro: return True
                continue
            current=value.kind
            while current:
                if current==item.name:return True
                current=_EXCEPTIONS.get(current)
        return False

    def call(self,callee,args,kwargs):
        self.tick("call")
        if isinstance(callee,Bound):return self.call(callee.function,[callee.receiver,*args],kwargs)
        if isinstance(callee,Function):
            scanner = _ScopeOperations()
            for statement in callee.node.body: scanner.visit(statement)
            if scanner.global_binding: raise Unsupported("global/nonlocal function binding")
            if isinstance(callee.node,ast.AsyncFunctionDef):
                if scanner.suspends: raise Unsupported("async generator continuation")
                from .heap_coroutines import create
                return create(self,callee,args,kwargs)
            if self.depth>=self.budget.max_call_depth:raise BudgetExceeded("call depth budget exhausted")
            env=Env(callee.closure,local_names=callee.local_names,function=True,mangle=callee.mangle,qualname=callee.qualname)
            self.bind_arguments(callee,args,dict(kwargs),env)
            if scanner.suspends:
                from .heap_continuations import create_heap_generator
                return create_heap_generator(self,callee,env)
            self.depth+=1;self.calls.append(callee.qualname)
            try:
                try:self.block(callee.node.body,env)
                except Signal as signal:
                    if signal.kind=="return":return signal.value
                    raise
                return None
            finally:self.depth-=1
        if isinstance(callee,Class):
            if callee.metaclass is not None:
                hook = self.class_attr(callee.metaclass,"__call__")
                if hook is not _MISSING:
                    if not isinstance(hook,Function): raise Unsupported("nonfunction metaclass call")
                    return self.call(Bound(hook,callee),args,kwargs)
            if callee.is_metaclass: return self.construct_class(callee,args,kwargs)
            if callee.exception_base:
                if kwargs or any(self.class_attr(callee,n) is not _MISSING for n in ("__new__","__init__")):
                    raise Unsupported("custom exception construction")
                return ExceptionValue(callee.exception_base,tuple(args),cls=callee)
            if any(self.class_attr(callee,name) is not _MISSING for name in ("__new__","__del__")):
                raise Unsupported("custom instance allocation/lifetime")
            obj=self.allocate(Instance(callee));init=self.class_attr(callee,"__init__")
            if init is not _MISSING:
                result=self.call(self.bind_descriptor(init,obj,callee),args,kwargs)
                if result is not None:self.fail("TypeError","__init__ must return None")
            elif args or kwargs:self.fail("TypeError","object takes no arguments")
            return obj
        if isinstance(callee,Instance):
            if self.class_attr(callee.cls,"__call__") is _MISSING:self.fail("TypeError","not callable")
            return self.call(self.type_method(callee,"__call__"),args,kwargs)
        if isinstance(callee,Descriptor):raise Unsupported("direct descriptor invocation")
        if isinstance(callee,Method):return self.method(callee,args,kwargs)
        if isinstance(callee,Builtin):return self.builtin(callee.name,args,kwargs)
        self.fail("TypeError","not callable")

    def bind_arguments(self,function,args,kwargs,env):
        spec=function.node.args
        positional=[_mangle(a.arg,function.mangle) for a in (*spec.posonlyargs,*spec.args)]
        posonly={_mangle(a.arg,function.mangle) for a in spec.posonlyargs}
        if len(args)>len(positional) and spec.vararg is None:self.fail("TypeError","too many positional arguments")
        for name,value in zip(positional,args):env.values[name]=value
        if spec.vararg:env.bind(spec.vararg.arg,self.allocate(Seq(list(args[len(positional):]),False)))
        for name in positional:
            if name in kwargs and name not in posonly:
                if name in env.values:self.fail("TypeError","multiple argument values")
                env.values[name]=kwargs.pop(name)
        for name,value in zip(positional[len(positional)-len(function.defaults):],function.defaults):
            env.values.setdefault(name,value)
        if any(name not in env.values for name in positional):self.fail("TypeError","missing positional argument")
        for arg in spec.kwonlyargs:
            name=_mangle(arg.arg,function.mangle)
            if name in kwargs:env.values[name]=kwargs.pop(name)
            elif name in function.kw_defaults:env.values[name]=function.kw_defaults[name]
            else:self.fail("TypeError","missing keyword-only argument")
        if spec.kwarg:env.bind(spec.kwarg.arg,self.allocate(Mapping(kwargs)))
        elif kwargs:self.fail("TypeError","unexpected keyword")

    def builtin(self,name,args,kwargs):
        if name == "object":
            if args or kwargs: self.fail("TypeError", "object takes no arguments")
            return self.allocate(OpaqueObject())
        if name in _EXCEPTIONS:
            if kwargs:self.fail("TypeError","exception keyword argument")
            return ExceptionValue(name,tuple(args))
        if name in {"classmethod","staticmethod"} and len(args)==1 and not kwargs:
            if name=="classmethod" and not isinstance(args[0],Function):raise Unsupported("classmethod delegation to a nonfunction descriptor")
            return self.allocate(Descriptor(args[0],name))
        if name=="type" and len(args)==3:
            return self.new_class(None,*args,kwargs=kwargs)
        if name=="type" and len(args)==1 and not kwargs:
            value=args[0]
            if isinstance(value,Instance):return value.cls
            if isinstance(value,Class):return value.metaclass or Builtin("type")
            if isinstance(value,OpaqueObject):return Builtin("object")
            if isinstance(value,ExceptionValue):return value.cls or Builtin(value.kind)
            if isinstance(value,Seq):return Builtin("list" if value.mutable else "tuple")
            if isinstance(value,Mapping):return Builtin("dict")
            if isinstance(value,RangeValue):return Builtin("range")
            if value is None:return Builtin("NoneType")
            if type(value) in (str,int,float,bool):return Builtin(type(value).__name__)
            raise Unsupported("type of modeled callable")
        if name in {"getattr","hasattr"} and len(args) in (2,3) and not kwargs:
            if name=="hasattr" and len(args)!=2:self.fail("TypeError","hasattr arity")
            try:result=self.getattr(args[0],args[1])
            except Signal as signal:
                if signal.kind=="raise" and signal.value.kind=="AttributeError":
                    if name=="hasattr":return False
                    if len(args)==3:return args[2]
                raise
            return True if name=="hasattr" else result
        if name=="setattr" and len(args)==3 and not kwargs:self.setattr(*args);return None
        if name=="isinstance" and len(args)==2 and not kwargs:return self.isinstance(args[0],args[1])
        if name=="callable" and len(args)==1 and not kwargs:
            if isinstance(args[0],Descriptor):raise Unsupported("descriptor callability")
            return isinstance(args[0],(Function,Bound,Class,Builtin,Method)) or isinstance(args[0],Instance) and self.class_attr(args[0].cls,"__call__") is not _MISSING
        if name=="len" and len(args)==1 and not kwargs:
            value=args[0]
            if isinstance(value,(Seq,Mapping,RangeValue)):return len(value.values)
            if isinstance(value,View):return len(value.receiver.values)
            if type(value) is str:return len(value)
            raise Unsupported("custom len protocol")
        if name in {"list","tuple"} and len(args)<=1 and not kwargs:
            if name=="tuple" and args and isinstance(args[0],Seq) and not args[0].mutable:return args[0]
            return self.allocate(Seq(list(self.elements(args[0])) if args else [],name=="list"))
        if name=="dict" and len(args)<=1:
            value=self.allocate(Mapping())
            if args:
                if isinstance(args[0],Mapping):value.values.update(args[0].values)
                else:
                    for item in self.elements(args[0]):
                        pair=list(self.elements(item))
                        if len(pair)!=2:self.fail("ValueError","dictionary update item length")
                        value.values[self.key(pair[0])]=pair[1]
            value.values.update(kwargs);return value
        if name=="reversed" and len(args)==1 and not kwargs:
            if isinstance(args[0], Seq): return self.allocate(Iterator(args[0], "reversed_seq", index=len(args[0].values)-1))
            raise Unsupported("custom reversed protocol")
        if name=="iter" and len(args)==1 and not kwargs:return self.iterate(args[0])
        if name=="next" and len(args) in (1,2) and not kwargs:
            from .heap_continuations import HeapGenerator
            if not isinstance(args[0],(Iterator,HeapGenerator)):raise Unsupported("custom next protocol")
            try:return self.next_value(args[0])
            except Signal as signal:
                if len(args)==2 and signal.kind=="raise" and signal.value.kind=="StopIteration":return args[1]
                raise
        if name=="range" and 1<=len(args)<=3 and not kwargs and all(type(a) is int for a in args):
            try:value=range(*args)
            except ValueError:self.fail("ValueError","range step")
            if len(value)>self.budget.max_steps:raise BudgetExceeded("range allocation budget")
            return self.allocate(RangeValue(value))
        if name=="bool" and len(args)<=1 and not kwargs:return self.truth(args[0]) if args else False
        if name in {"str","repr"} and len(args)==1 and not kwargs:
            value=args[0]
            if value is not None and type(value) not in (str,int,float,bool):raise Unsupported("custom conversion")
            return str(value) if name=="str" else repr(value)
        raise Unsupported("builtin "+name)

    def isinstance(self,value,kind):
        if isinstance(value,Instance) and any(self.class_attr(value.cls,name) is not _MISSING for name in ("__class__","__getattribute__")):
            raise Unsupported("custom instance class observation")
        if isinstance(kind,Seq) and not kind.mutable:return any(self.isinstance(value,k) for k in kind.values)
        if isinstance(kind,Class):
            if kind.metaclass is not None and self.class_attr(kind.metaclass, "__instancecheck__") is not _MISSING: raise Unsupported("custom metaclass instance check")
            return (isinstance(value,(Instance,ExceptionValue)) and value.cls is not None and kind in value.cls.mro) or isinstance(value,Class) and value.metaclass is not None and kind in value.metaclass.mro
        if isinstance(kind,Builtin):
            name=kind.name
            if name=="object":return True
            if name=="type":return isinstance(value,Class) or isinstance(value,Builtin) and value.name in ({"object","type","str","int","float","bool","dict","list","tuple","range","classmethod","staticmethod","NoneType"}|set(_EXCEPTIONS))
            if name=="str":return type(value) is str
            if name=="int":return type(value) in (int,bool)
            if name=="bool":return type(value) is bool
            if name=="float":return type(value) is float
            if name in {"tuple","list"}:return isinstance(value,Seq) and value.mutable==(name=="list")
            if name=="dict":return isinstance(value,Mapping)
            if name=="range":return isinstance(value,RangeValue)
            if name in _EXCEPTIONS: return isinstance(value, ExceptionValue) and self.exception_matches(value, kind)
        raise Unsupported("custom/native isinstance protocol")

    def method(self,method,args,kwargs):
        obj,name=method.receiver,method.name
        from .heap_continuations import HeapGenerator,resume_heap_generator
        from .heap_coroutines import Coroutine,method as coroutine_method
        if isinstance(obj,Coroutine): return coroutine_method(self,obj,name,args,kwargs)
        if isinstance(obj,HeapGenerator):
            if kwargs:self.fail("TypeError","generator method keywords")
            if name=="__iter__" and not args:return obj
            return resume_heap_generator(self,obj,"next" if name=="__next__" else name,args)
        if isinstance(obj,Function) and name=="__call__": return self.call(obj,args,kwargs)
        if isinstance(obj,Builtin) and obj.name=="type":
            if name=="__new__" and len(args)==4:
                meta = None if args[0] == Builtin("type") else args[0]
                return self.new_class(meta,*args[1:],kwargs=kwargs)
            if name=="__init__" and len(args)==4 and isinstance(args[0],Class):
                return None
            if name=="__prepare__" and len(args)==2:
                return self.allocate(Mapping())
            self.fail("TypeError","type protocol signature")
        if isinstance(obj,Descriptor) and name=="__get__" and len(args) in (1,2) and not kwargs:
            owner=args[1] if len(args)>1 else args[0].cls if isinstance(args[0],Instance) else None
            if owner is None:raise Unsupported("descriptor owner inference")
            return self.bind_descriptor(obj,args[0],owner)
        if isinstance(obj,Seq) and obj.mutable:
            if name=="append" and len(args)==1 and not kwargs:obj.values.append(args[0]);return None
            if name=="extend" and len(args)==1 and not kwargs:
                for value in self.elements(args[0]):obj.values.append(value)
                return None
            if name=="pop" and len(args)<=1 and not kwargs:
                index=args[0] if args else -1
                if type(index) not in (int,bool):raise Unsupported("custom list index protocol")
                try:return obj.values.pop(index)
                except IndexError:self.fail("IndexError",index)
            if name=="reverse" and not args and not kwargs:obj.values.reverse();return None
            if name=="copy" and not args and not kwargs:return self.allocate(Seq(list(obj.values)))
        if isinstance(obj,Mapping):
            if name in {"items","keys","values"} and not args and not kwargs:return self.allocate(View(obj,name))
            if name in {"get","setdefault"} and len(args) in (1,2) and not kwargs:
                key=self.key(args[0]);default=args[1] if len(args)==2 else None
                if name=="setdefault" and key not in obj.values:
                    if obj.readonly:self.fail("TypeError","readonly mapping")
                    obj.values[key]=default
                return obj.values.get(key,default)
            if name=="copy" and not args and not kwargs:return self.allocate(Mapping(dict(obj.values)))
            if name=="update" and len(args)<=1:
                if obj.readonly:self.fail("TypeError","readonly mapping")
                if args:
                    if isinstance(args[0],Mapping):obj.values.update(args[0].values)
                    else:
                        for item in self.elements(args[0]):
                            pair=list(self.elements(item))
                            if len(pair)!=2:self.fail("ValueError","dictionary update item length")
                            obj.values[self.key(pair[0])]=pair[1]
                obj.values.update(kwargs);return None
        if type(obj) is str:
            if kwargs:raise Unsupported("string keyword arguments")
            if name in {"startswith","endswith"} and len(args)==1:
                prefix=args[0]
                if isinstance(prefix,Seq) and not prefix.mutable:prefix=tuple(prefix.values)
                if type(prefix) not in (str,tuple):self.fail("TypeError","prefix type")
                try:return obj.startswith(prefix) if name=="startswith" else obj.endswith(prefix)
                except TypeError:self.fail("TypeError","prefix type")
            if name in {"upper","lower"} and not args:return obj.upper() if name=="upper" else obj.lower()
            if name=="join" and len(args)==1:
                elements=list(self.elements(args[0]))
                if not all(type(x) is str for x in elements):self.fail("TypeError","join member")
                if sum(map(len,elements))+len(obj)*max(len(elements)-1,0)>1_000_000:raise BudgetExceeded("string allocation budget")
                return obj.join(elements)
            if name=="replace" and 2<=len(args)<=3 and all(type(x) is str for x in args[:2]):
                if len(args)==3 and type(args[2]) not in (int,bool):raise Unsupported("replace count protocol")
                if (len(obj)+1)*len(args[1])+len(obj)>1_000_000:raise BudgetExceeded("string allocation budget")
                return obj.replace(*args)
        raise Unsupported("method "+name)

    def export(self,value,seen=None):
        seen=set() if seen is None else seen
        if type(value) is float and not math.isfinite(value):raise Unsupported("nonfinite result observation")
        if value is None or type(value) in (bool,int,float,str):return value
        if id(value) in seen:raise Unsupported("cyclic result observation")
        seen=seen|{id(value)}
        if isinstance(value,Seq):
            if not value.mutable:raise Unsupported("tuple observations need a typed claim schema")
            return [self.export(v,seen) for v in value.values]
        if isinstance(value,Mapping):
            if any(type(key) is not str for key in value.values):raise Unsupported("nonstring result key")
            return {key:self.export(val,seen) for key,val in value.values.items()}
        raise Unsupported("nonliteral result observation")


class _Traceback:
    pass


def analyze_closed_source(source: str, observed_globals, *, budget: Budget | None = None, await_globals=()):
    """Derive exact final observations in the declared finite closed model.

    Observed names select output projection only; their expected values do not
    influence source transitions. Rejected/unknown analyses retain prefix calls.
    """
    if budget is not None and type(budget) is not Budget: raise ValueError("budget must be an exact Budget value")
    if type(observed_globals) not in (list,tuple) or any(type(n) is not str or not n.isidentifier() for n in observed_globals):
        raise ValueError("observed_globals must be a list/tuple of identifiers")
    if len(set(observed_globals))!=len(observed_globals):raise ValueError("duplicate observed global")
    from .heap_coroutines import entry_binding, await_value
    entry_contract, entry_sha256 = entry_binding(await_globals)
    digest=hashlib.sha256(source.encode()).hexdigest() if type(source) is str else ""
    result={"semantics_version":SEMANTICS_VERSION,"source_sha256":digest,"contract":list(CONTRACT),
            "status":"unsupported","observations":{},"normal_module_completion":False,
            "entry_contract":entry_contract,"entry_sha256":entry_sha256,"normal_entry_completion":False,
            "full_python_soundness_proved":False,"mechanized_semantics_proof":False,
            "kcfa_results_certified":False,"runtime_observations_used":False,
            "residual":["No conclusion for open inputs, future invocations or operations outside the declared model."]}
    machine=None
    try:
        machine=ClosedHeapMachine(source,budget or Budget())
        machine.block(machine.tree.body,machine.env)
        result["normal_module_completion"]=True
        for name in await_globals: await_value(machine,machine.env.lookup(name))
        if any(generator.lifecycle=="suspended" for generator in machine.heap_generators):
            raise Unsupported("suspended generator finalization outside closed model")
        result["normal_entry_completion"]=True
        result["observations"]={name:machine.export(machine.env.lookup(name)) for name in observed_globals}
        result["normal_module_completion"]=True;result["status"]="closed_model_completed"
    except Unsupported as exc:result["diagnostic"]=str(exc)
    except BudgetExceeded as exc:result["status"]="unknown";result["diagnostic"]=str(exc)
    except Signal as signal:
        result["status"]="model_exception";result["diagnostic"]=signal.value.kind if isinstance(signal.value,ExceptionValue) else signal.kind
    except (SyntaxError,ValueError) as exc:result["diagnostic"]=type(exc).__name__+": "+str(exc)
    except (RecursionError,OverflowError):result["status"]="unknown";result["diagnostic"]="host numeric or recursion limit"
    if machine:
        result.update(steps=machine.steps,objects=machine.objects,operation_kinds=sorted(machine.operations),prefix_calls=list(machine.calls))
    return result


def check_closed_effects(source: str, candidate: dict, *, budget: Budget | None = None, await_globals=()):
    """Check source-bound literal final-state claims without trusting proposals."""
    if type(source) is not str:
        return {"status":"rejected","diagnostic":"source must be text","conditional_effects_verified":0}
    from .heap_coroutines import entry_binding
    try: entry_contract, entry_sha256 = entry_binding(await_globals)
    except ValueError as exc:
        return {"status":"rejected","diagnostic":str(exc),"conditional_effects_verified":0}
    expected_keys={"source_sha256","semantics_version","globals"} | ({"entry_sha256"} if await_globals else set())
    if type(candidate) is not dict or any(type(k) is not str for k in candidate) or set(candidate)!=expected_keys:
        return {"status":"rejected","diagnostic":"invalid candidate schema","conditional_effects_verified":0}
    if type(candidate["semantics_version"]) is not str or type(candidate["source_sha256"]) is not str or candidate["semantics_version"]!=SEMANTICS_VERSION or candidate["source_sha256"]!=hashlib.sha256(source.encode()).hexdigest():
        return {"status":"rejected","diagnostic":"source/semantics binding mismatch","conditional_effects_verified":0}
    if await_globals and (type(candidate["entry_sha256"]) is not str or candidate["entry_sha256"]!=entry_sha256):
        return {"status":"rejected","diagnostic":"entry binding mismatch","conditional_effects_verified":0}
    claims=candidate["globals"]
    if type(claims) is not dict or not claims or any(type(name) is not str or not name.isidentifier() for name in claims):
        return {"status":"rejected","diagnostic":"claims must be nonempty global projections","conditional_effects_verified":0}
    try:
        _validate_literal(claims)
    except (ValueError,RecursionError) as exc:
        return {"status":"rejected","diagnostic":str(exc),"conditional_effects_verified":0}
    result=analyze_closed_source(source,list(claims),budget=budget,await_globals=await_globals)
    if result["status"]=="closed_model_completed":
        matches={name:_literal_equal(result["observations"][name],value) for name,value in claims.items()}
        result["claim_matches"]=matches
        result["status"]="conditional_verified" if all(matches.values()) else "rejected"
        result["conditional_effects_verified"]=len(claims) if all(matches.values()) else 0
    else:result["conditional_effects_verified"]=0
    return result


def _validate_literal(value, seen=None, counter=None):
    seen=set() if seen is None else seen
    counter=[0] if counter is None else counter
    counter[0]+=1
    if counter[0]>100_000:raise ValueError("claim budget exhausted")
    if value is None or type(value) in (bool,int,str):return
    if type(value) is float and math.isfinite(value):return
    if type(value) not in (list,dict):raise ValueError("claims must contain finite JSON literals")
    if id(value) in seen:raise ValueError("cyclic claim")
    if type(value) is dict and any(type(key) is not str for key in value):raise ValueError("claim keys must be strings")
    for item in value.values() if type(value) is dict else value:
        _validate_literal(item,seen|{id(value)},counter)


def _literal_equal(left,right):
    if type(left) is not type(right):return False
    if type(left) is float:return left.hex()==right.hex()
    if type(left) is dict:return left.keys()==right.keys() and all(_literal_equal(left[key],right[key]) for key in left)
    if type(left) is list:return len(left)==len(right) and all(_literal_equal(a,b) for a,b in zip(left,right))
    return left==right
