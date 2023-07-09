from typing import Optional, Set, List, Dict

from pythonstan.utils.persistent_rb_tree import PersistentMap
from pythonstan.ir import IRClass, IRScope
from pythonstan.graph.cfg import BaseBlock
from .scope_chain import ScopeChain, Scope
from .value import Value
from .obj_label import ObjLabel


class Obj:
    cls: Optional[ObjLabel]

    scope_chain: Optional[ScopeChain]
    internal_value: 'Value'
    default_other_property: 'Value'

    base_classes: Optional[List[ObjLabel]]
    self_scope: Scope

    @property
    def the_none(self) -> 'Obj':
        return ObjDefaults.the_none

    @property
    def the_absent_modified(self) -> 'Obj':
        return ObjDefaults.the_absent_modified

    @property
    def the_none_modified(self) -> 'Obj':
        return ObjDefaults.the_none_modified

    @property
    def the_unknown(self) -> 'Obj':
        return ObjDefaults.the_unknown

    def __init__(self, scope_chain: Optional[ScopeChain] = None,
                 cls: Optional[ObjLabel] = None,
                 base_classes: Optional[List[ObjLabel]] = None):
        self.scope_chain = scope_chain
        self.cls = cls
        self.base_classes = base_classes

    @classmethod
    def make_cls(cls, scope_chain: ScopeChain) -> 'Obj':
        ret = cls()


    def is_cls(self):
        return self.cls is not None

    def get_property(self, prop: str) -> Optional[Value]:
        return self.properties.get(prop)

    def get_static_property(self, prop: str) -> Optional[Value]:
        assert self.is_cls(), "Cannot retrive property from Object!"
        return self.static_properties.get(prop)

    def get_class_property(self, prop: str) -> Optional[Value]:
        assert self.is_cls(), "Cannot retrive property from Object!"
        return self.class_properties.get(prop)

    def get_bases_label(self) -> List[ObjLabel]:
        assert self.is_cls and self.base_classes is not None, "Cannot get bases from Object!"
        return self.base_classes

    @classmethod
    def from_obj(cls, o: 'Obj') -> 'Obj':
        ret = cls()

    def set_cls_label(self, cls: ObjLabel):
        self.cls = cls

    def get_cls_label(self) -> ObjLabel:
        return self.cls

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

    def maybe_class(self) -> bool:
        ...

    def restrict_class(self) -> 'Cls':
        ...


class ObjDefaults:
    '''
    the_none: 'Obj'
    the_absent_modified: 'Obj'
    the_none_modified: 'Obj'
    the_unknown: 'Obj'

    class Defaults:
        @classmethod
        def _make_the_none(cls) -> 'Obj':
            obj = Obj()
            obj.properties = PersistentMap()
            obj.default_other_property = obj.internal_value = Value.make_none()
            return obj

    @classmethod
    def _make_the_absent_modified(cls) -> 'Obj':
        obj = Obj()
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
    '''

    the_none = Obj()
    the_absent_modified = Obj()
    the_none_modified = Obj()
    the_unknown = Obj()
