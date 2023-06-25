from typing import Optional
from enum import Enum


class PropertyKind(Enum):
    STATIC = 0
    CLASS = 1
    INSTANCE = 2


class Property:
    name: Optional[str]
    kind: PropertyKind

    def __init__(self, kind: PropertyKind, name: Optional[str]):
        self.kind = kind
        self.name = name

    def get_kind(self) -> PropertyKind:
        return self.kind

    def get_name(self) -> str:
        return self.name

    def __hash__(self):
        return self.kind.__hash__() * 5 + \
            (0 if self.name is None else self.name.__hash__()) * 31

    def __eq__(self, other):
        if self == other:
            return True
        if other is None:
            return False
        if type(self) != type(other):
            return False
        if self.kind != other.kind:
            return False
        if self.name is None:
            if other.name is not None:
                return False
        elif self.name != other.name:
            return False
        return True
