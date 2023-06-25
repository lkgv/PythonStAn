from typing import Optional

from pythonstan.utils.persistent_rb_tree import PersistentMap
from pythonstan.ir import IRClass, IRScope
from .scope_chain import ScopeChain
from .value import Value


class Obj:
    scope_chain: Optional[ScopeChain]
    properties: PersistentMap[str, Value]
    writable_properties: bool
    writable: bool
    internal_value: 'Value'
    default_other_property: 'Value'

    number_of_objs_created = 0
    number_of_makewritable_properties = 0
    the_none: 'Obj'
    the_absent_modified: 'Obj'
    the_none_modified: 'Obj'
    the_unknown: 'Obj'

    @classmethod
    def _make_the_none(cls) -> 'Obj':
        obj = cls()
        obj.properties = PersistentMap()
        obj.default_other_property = obj.internal_value = Value.make_none()
        return obj

    @classmethod
    def _make_the_absent_modified(cls) -> 'Obj':
        obj = cls()
        obj.properties = PersistentMap()
        obj.default_other_property = obj.internal_value = Value.make_absent_modified()
        return obj

    @classmethod
    def _make_the_none_modified(cls) -> 'Obj':
        obj = cls()
        obj.properties = PersistentMap()
        obj.default_other_property = obj.internal_value = Value.make_none_modified()
        return obj

    @classmethod
    def _make_the_unknown(cls) -> 'Obj':
        obj = Obj()
        obj.properties = PersistentMap()
        obj.default_other_property = obj.internal_value = Value.make_unknown()
        obj.scope_chain = None
        return obj

    @classmethod
    def _make_default_values(cls):
        cls.the_none = cls._make_the_none()
        cls.the_absent_modified = cls._make_the_absent_modified()
        cls.the_none_modified = cls._make_the_none_modified()
        cls.the_unknown = cls._make_the_unknown()

    _make_default_values()

    def __init__(self):
        self.number_of_objs_created += 1

    @classmethod
    def from_obj(cls, o: 'Obj') -> 'Obj':
        ret = cls()

    def is_unknown(self) -> bool:
        for _, v in self.properties.items():
            if not v.is_unknown():
                return False
        return self.default_other_property.is_unknown() and \
            self.internal_value.is_unknown() and \
            self.scope_chain is None

    def is_all_none(self) -> bool:
        for _, v in self.properties.items():
            if not v.is_none():
                return False
        return self.default_other_property.is_none() and \
            self.internal_value.is_none() and \
            self.scope_chain is None

    def freeze(self) -> 'Obj':
        self.writable = False
        return self

    def get_type(self) -> Optional[IRClass]:
        ...

    def get_create_scope(self) -> IRScope:
        ...
