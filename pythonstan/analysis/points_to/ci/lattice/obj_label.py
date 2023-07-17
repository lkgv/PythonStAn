from enum import Enum
from typing import Optional, Set

from pythonstan.graph.cfg import BaseBlock
from pythonstan.ir import IRScope
from .context import Context


class Renamings:
    maybe_summarized: Set['ObjLabel']
    definitely_summarized: Set['ObjLabel']

    def __init__(self):
        self.maybe_summarized = {*()}
        self.definitely_summarized = {*()}

    @classmethod
    def from_renamings(cls, s: 'Renamings') -> 'Renamings':
        ret = cls()
        ret.maybe_summarized = s.maybe_summarized
        ret.definitely_summarized = s.definitely_summarized
        return ret

    @classmethod
    def from_obj_label(cls, l: 'ObjLabel') -> 'Renamings':
        ret = cls()
        ret.maybe_summarized.add(l)
        ret.definitely_summarized.add(l)
        return ret

    def remove_summarized(self, l: 'ObjLabel', strong: bool):
        self.definitely_summarized.remove(l)
        if strong:
            self.maybe_summarized.remove(l)

    def add_definitely_summarized(self, l: 'ObjLabel'):
        self.definitely_summarized.add(l)
        self.maybe_summarized.add(l)

    def is_maybe_new(self, l: 'ObjLabel') -> bool:
        return l in self.maybe_summarized

    def is_definitely_new(self, l: 'ObjLabel') -> bool:
        return l in self.definitely_summarized

    def clear(self):
        self.maybe_summarized.clear()
        self.definitely_summarized.clear()

    def join(self, s: 'Renamings') -> bool:
        result = False
        if self.maybe_summarized.union(s.maybe_summarized) != self.maybe_summarized:
            self.maybe_summarized.update(s.maybe_summarized)
            result = True
        if self.definitely_summarized.intersection(s.definitely_summarized) != self.definitely_summarized:
            self.definitely_summarized.intersection_update(s.definitely_summarized)
            result = True
        return result

    def add(self, s: 'Renamings') -> bool:
        result = False
        if self.maybe_summarized.union(s.maybe_summarized) != self.maybe_summarized:
            self.maybe_summarized.update(s.maybe_summarized)
            result = True
        if self.definitely_summarized.union(s.definitely_summarized) != self.definitely_summarized:
            self.definitely_summarized.update(s.definitely_summarized)
            result = True
        return result

    def __eq__(self, s: 'Renamings') -> bool:
        if not isinstance(s, Renamings):
            return False
        return self.maybe_summarized == s.maybe_summarized and \
            self.definitely_summarized == s.definitely_summarized

    def __str__(self):
        return f"maybe={self.maybe_summarized}, definitely={self.definitely_summarized}"

    def rename_obj_label(self, l: 'ObjLabel') -> Set['ObjLabel']:
        if l.is_singleton() and self.is_maybe_new(l):
            if not self.is_definitely_new(l):
                return {l, l.make_summary()}
            else:
                return {l.make_summary()}
        else:
            return {l}

    def rename_obj_labels(self, ls: Set['ObjLabel']) -> Set['ObjLabel']:
        if not any(l for l in ls if l.is_singleton() and self.is_maybe_new(l)):
            return ls
        new_ls = {*()}
        for l in ls:
            if l.is_singleton() and self.is_maybe_new(l):
                new_ls.add(l.make_summary())
                if not self.is_definitely_new(l):
                    new_ls.add(l)
            else:
                new_ls.add(l)
        return new_ls

    def rename_inverse(self, prop: 'ObjProperty') -> Set['ObjProperty']:
        res = {prop}
        l = prop.get_obj_label()
        if not l.is_singleton() and self.is_maybe_new(l.make_singleton()):
            res.add(prop.make_singleton())
        return res


class ObjProperty:
    obj_label: 'ObjLabel'
    property: str

    def __init__(self, obj_label: 'ObjLabel', property: str):
        self.obj_label = obj_label
        self.property = property

    def make_renamed(self, l: 'ObjLabel'):
        return ObjProperty(l, self.property)

    def get_obj_label(self) -> 'ObjLabel':
        return self.obj_label

    def get_property(self) -> str:
        return self.property

    def make_singleton(self) -> 'ObjProperty':
        return ObjProperty(self.obj_label.make_singleton(), property)


class LabelKind(Enum):
    Object = 1
    Function = 1
    Class = 3
    List = 4
    Tuple = 5
    Set = 6
    Dict = 7
    Int = 8
    Float = 9
    Str = 10
    Bool = 11
    Error = 12


class ObjLabel:
    host_obj_label: Optional['ObjLabel']
    block: Optional[BaseBlock]
    scope: Optional[IRScope]
    heap_ctx: Context
    singleton: bool
    kind: LabelKind

    def __init__(self, block: Optional[BaseBlock], scope: Optional[IRScope], kind: LabelKind,
                 heap_ctx: Optional[Context] = None, singleton: bool = True,
                 host_obj_label: Optional['ObjLabel'] = None):
        self.block = block
        self.scope = scope
        self.kind = kind
        self.heap_ctx = Context.make_empty() if heap_ctx is None else heap_ctx
        self.singleton = singleton
        self.host_obj_label = host_obj_label

    def __eq__(self, other) -> bool:
        return (self.block == other.block and self.scope == other.scope and
                self.kind == other.kind and self.heap_ctx == other.heap_ctx and
                self.singleton == other.singleton and
                self.host_obj_label == other.host_obj_label)
    @classmethod
    def make(cls, block: Optional[BaseBlock], kind: LabelKind,
             host_obj_label: Optional['ObjLabel'] = None) -> 'ObjLabel':
        return cls(block, None, kind, None, True, host_obj_label)

    @classmethod
    def make_with_ctx(cls, block: Optional[BaseBlock], kind: LabelKind, heap_ctx: Context,
                      host_obj_label: Optional['ObjLabel'] = None) -> 'ObjLabel':
        return cls(block, None, kind, heap_ctx, True, host_obj_label)

    @classmethod
    def make_scope(cls, scope: IRScope, host_obj_label: Optional['ObjLabel'] = None) -> 'ObjLabel':
        return cls(None, scope, LabelKind.Function, None, True, host_obj_label)

    @classmethod
    def make_scope_with_ctx(cls, scope: IRScope, heap_ctx: Context,
                            host_obj_label: Optional['ObjLabel'] = None) -> 'ObjLabel':
        return cls(None, scope, LabelKind.Function, heap_ctx, True, host_obj_label)

    def set_host_obj_label(self, host_obj_label: 'ObjLabel'):
        self.host_obj_label = host_obj_label

    def get_host_obj_label(self) -> 'ObjLabel':
        assert self.host_obj_label is not None, "Host Obj Label of a method label cannot be None!"
        return self.host_obj_label

    def get_scope(self) -> IRScope:
        assert self.scope is not None, "Scope of a function/class label cannot be None!"
        return self.scope

    def get_kind(self) -> LabelKind:
        return self.kind

    def get_call_block(self) -> Optional[BaseBlock]:
        return self.block

    def is_singleton(self) -> bool:
        return self.singleton

    def get_heap_context(self) -> Context:
        return self.heap_ctx

    def make_summary(self) -> 'ObjLabel':
        assert self.is_singleton(), "Attempt to obtain summary of non-singleton"
        return ObjLabel(self.block, self.scope, self.kind, self.heap_ctx, False)

    def make_singleton(self) -> 'ObjLabel':
        if self.is_singleton():
            return self
        return ObjLabel(self.block, self.scope, self.kind, self.heap_ctx, True)

    @staticmethod
    def allow_strong_update(objs: Set['ObjLabel']) -> bool:
        return len(objs) == 1 and next(iter(objs)).is_singleton()
