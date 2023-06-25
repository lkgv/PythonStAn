from enum import Enum
from typing import Optional, Set

from pythonstan.graph.cfg import BaseBlock


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
    Dict = 6
    Int = 7
    Float = 8
    Str = 9
    Bool = 10
    Error = 11


class ObjLabel:
     source: Optional[BaseBlock]
     is_singleton: bool
     kind: LabelKind

     def __init__(self, ):
         ...
