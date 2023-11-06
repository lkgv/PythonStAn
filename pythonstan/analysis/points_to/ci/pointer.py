from typing import Set
from abc import abstractmethod

from pythonstan.ir import *


class Obj:
    cls: IRClass
    alloc: IRStatement

    def __init__(self, cls, alloc):
        self.cls = cls
        self.alloc = alloc

    def get_cls(self) -> IRClass:
        return self.cls

    def get_alloc(self) -> IRStatement:
        return self.alloc


class Pointer:
    pts: Set[Obj]

    @abstractmethod
    def __init__(self):
        self.pts = set()

    def get_pts(self) -> Set[Obj]:
        return self.pts


class Var(Pointer):
    name: str

    def __init__(self, name: str):
        self.name = name
        super().__init__()

    def get_name(self) -> str:
        return self.name

    def get_store_attrs(self) -> Set[IRStoreAttr]:
        ...

    def get_load_attrs(self) -> Set[IRLoadAttr]:
        ...

    def get_calls(self) -> Set[IRCall]:
        ...


class ObjAttr(Pointer):
    base: Obj
    attr: str

    def __init__(self, base: Obj, attr: str):
        self.base = base
        self.attr = attr

    def get_base(self) -> Obj:
        return self.base

    def get_attr(self) -> str:
        return self.attr

