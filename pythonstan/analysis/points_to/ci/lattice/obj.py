from typing import Optional, Set, List, Dict

from pythonstan.utils.persistent_rb_tree import PersistentMap
from pythonstan.ir import IRClass, IRScope
from pythonstan.graph.cfg import BaseBlock
from .scope_chain import ScopeChain
from .value import Value
from .obj_label import ObjLabel


class Cls:
    name: str
    ir: Optional[IRClass]
    is_internal: bool
    bases: List['Cls']
    static_properties: Dict[str, Value]
    class_properties: Dict[str, Value]
    instance_properties: Dict[str, Value]


    def __init__(self, name: str, ir: Optional[IRClass], is_internal: bool = False):
        self.name = name
        self.ir = ir
        self.is_internal = is_internal
        self.bases = []
        self.static_properties = {}
        self.class_properties = {}
        self.instance_properties = {}

    @classmethod
    def make_internal_cls(cls, name: str) -> 'Cls':
        return cls(name, None, True)

    @classmethod
    def make_user_defined_cls(cls, ir: IRClass) -> 'Cls':
        return cls(ir.get_name(), ir, False)

    def get_bases(self) -> List['Cls']:
        return self.bases

    def add_base(self, base: 'Cls'):
        self.bases.append(base)

    @staticmethod
    def _retrive_property(cls: 'Cls', pkey: str, kind: str) -> Optional[Value]:
        assert kind in ['static_properties', 'class_properties', 'instance_properties']
        if pkey in getattr(cls, kind):
            return getattr(cls, kind)[pkey]
        for base_cls in cls.get_bases():
            prop = base_cls._retrive_property(base_cls, pkey, kind)
            if prop is not None:
                return prop
        return None

    def get_static_property(self, pkey: str) -> Optional[Value]:
        return self._retrive_property(self, pkey, 'static_properties')

    def get_class_property(self, pkey: str) -> Optional[Value]:
        return self._retrive_property(self, pkey, 'class_properties')

    def get_instance_property(self, pkey: str) -> Optional[Value]:
        return self._retrive_property(self, pkey, 'instance_properties')


class Obj:
    cls: ObjLabel

    scope_chain: Optional[ScopeChain]
    properties: Dict[str, Value]
    static_properties: Optional[Dict[str, Value]]
    class_properties: Optional[Dict[str, Value]]

    writable_properties: bool
    writable: bool
    internal_value: 'Value'
    default_other_property: 'Value'

    is_cls: bool
    base_classes: Optional[List[ObjLabel]]

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
    def make_cls(cls) -> 'Obj':
        ret = cls()
        ret.is_cls = True
        ret.static_properties = {}
        ret.class_properties = {}
        ret.base_classes = {}

    def is_cls(self):
        return self.is_cls

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
