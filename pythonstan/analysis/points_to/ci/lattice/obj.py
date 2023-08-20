from typing import Optional, Set, List, Dict

from pythonstan.utils.persistent_rb_tree import PersistentMap
from pythonstan.ir import IRClass, IRScope
from pythonstan.graph.cfg import BaseBlock
from .scope_chain import ScopeChain, Scope
from .value import Value
from .obj_label import ObjLabel, LabelKind
from .summarized import Summarized


class Obj:
    cls: Value

    properties: Dict[str, Value]
    writable_properties: bool
    default_property: Value

    # just for objs that have ``__getitem'' method
    items: Dict[Value, Value]  # map single value into a value
    default_item: Value  # defaultly theAbsent, if obj has ``__getitem__``, then theNone
    has_getitem: bool
    writable_items: bool

    ir_definition: Optional[IRScope]  # only for class and module, not for plain objects
    base_classes: List[Value]  # may be the summary of several classes

    scope_chain: Optional[ScopeChain]
    scope_unknown: bool

    def __init__(self, x: Optional['Obj'] = None, has_items: bool = False):
        if x is not None:
            self.set_to(x)
        else:
            self.properties = {}
            self.writable_properties = True
            self.default_property = Value.make_absent()
            self.items = {}
            self.default_item = Value.make_none()
            self.has_getitem = has_items
            self.writable_items = True
            self.ir_definition = None
            self.base_classes = []
            self.scope_chain = None
            self.scope_unknown = True

    def set_to(self, x: 'Obj'):
        self.properties = x.properties
        self.default_property = x.default_property
        x.writable_properties = self.writable_properties = False
        self.items = x.items
        self.default_item = x.default_item
        self.has_getitem = x.has_getitem
        self.writable_items = x.writable_items = False

        self.cls = x.cls
        self.ir_definition = x.ir_definition
        self.base_classes = x.base_classes

        self.scope_chain = x.scope_chain
        self.scope_unknown = x.scope_unknown

    @classmethod
    def make_class(cls, ir_definition: IRScope) -> 'Obj':
        obj = cls()
        obj.ir_definition = ir_definition
        obj.base_classes = []
        return obj

    @classmethod
    def make_unknown(cls) -> 'Obj':
        obj = cls()
        obj.writable_properties = obj.writable_items = False
        obj.scope_unknown = True
        obj.default_property = obj.default_item = Value.make_unknown()
        return obj

    @classmethod
    def make_none(cls) -> 'Obj':
        obj = cls()
        obj.default_property = obj.default_item = Value.make_none()
        obj.scope_unknown = False
        return obj

    @classmethod
    def make_absent(cls) -> 'Obj':
        obj = cls()
        obj.default_property = obj.default_item = Value.make_absent()
        obj.scope_unknown = False
        return obj

    def add_base_class(self, base_class: Value):
        self.base_classes.append(base_class)

    def add_base_classes(self, base_classes: List[Value]):
        self.base_classes = [x for x in base_classes]

    def get_class(self) -> Value:
        return self.cls

    def is_maybe_class(self) -> bool:
        return self.cls.is_maybe_none() and len(self.base_classes) > 0

    def get_ir_definition(self) -> Optional[IRScope]:
        return self.ir_definition

    def is_unknown(self) -> bool:
        for v in self.properties.values():
            if v.is_unknown():
                return False
        return self.default_property.is_unknown() and \
               (not self.has_getitem or (self.has_getitem and self.default_item.is_unknown())) and \
               self.cls.is_unknown() and self.scope_unknown

    def is_none(self):
        for v in self.properties.values():
            if v.is_none():
                return False
        return self.default_property.is_none() and \
               (not self.has_getitem or (self.has_getitem and self.default_item.is_none())) and \
               self.cls.is_unknown() and not self.scope_unknown and self.scope_chain is None

    def summarize(self, s: Summarized):
        self.cls = self.cls.summarize(s)
        self.properties = {k: v.summarize(s) for k, v in self.properties}
        self.writable_properties = True
        self.default_property = self.default_property.summarize(s)
        if self.has_getitem:
            self.items = {k: v.summarize(s) for k, v in self.items}
            self.default_item = self.default_item.summarize(s)
        if len(self.base_classes) > 0:
            self.base_classes = [cls.summarize(s) for cls in self.base_classes]
        if self.scope_chain is not None:
            self.scope_chain = ScopeChain.summarize(self.scope_chain, s)

    def replace_unmodified_parts(self, other: 'Obj'):
        new_properties = {}
        for k, v in self.properties:
            if not v.is_maybe_modified:
                v = other.properties.get(k)
            if v is not None:
                new_properties[k] = v
        if not self.default_property.is_maybe_modified():
            for k, v in other.properties:
                if k not in new_properties:
                    new_properties[k] = v
            self.default_property = other.default_property
        self.properties = new_properties
        self.writable_properties = True
        if self.has_getitem:
            new_items = {}
            for k, v in self.items:
                if not v.is_maybe_modified:
                    v = other.properties.get(k)
                if v is not None:
                    new_items[k] = v
            if not self.default_item.is_maybe_modified():
                for k, v in other.items:
                    if k not in new_items:
                        new_items[k] = v
                self.default_item = other.default_item
            self.items = new_items
            self.writable_items = True
        if not self.cls.is_maybe_modified():
            self.cls = other.cls
        if self.scope_unknown and not other.scope_unknown:
            self.scope_chain = other.scope_chain
            self.scope_unknown = other.scope_unknown

    def make_writable_properties(self):
        if not self.writable_properties:
            self.properties = {k: v for k, v in self.properties.items()}
            self.writable_properties = True

    def make_writable_items(self):
        if not self.writable_items:
            self.items = {k: v for k, v in self.items.items()}
            self.writable_items = True

    def clear_modified(self):
        self.properties = {k: v.restrict_to_not_modified() for k, v in self.properties.items()}
        self.writable_properties = True
        self.default_property = self.default_property.restrict_to_not_modified()
        if self.has_getitem:
            self.items = {k: v.restrict_to_not_modified() for k, v in self.items.items()}
            self.default_item = self.default_item.restrict_to_not_modified()
            self.writable_items = True
        self.cls = self.cls.restrict_to_not_modified()

    def get_property(self, name: str) -> Value:
        v = self.properties.get(name)
        if v is None:
            v = self.default_property
        return v

    def restrict_get_property(self, name: str) -> Optional[Value]:
        return self.properties.get(name)

    def get_default_property(self) -> Value:
        return self.default_property

    def set_property(self, name: str, v: Value):
        self.make_writable_properties()
        self.properties[name] = v

    def get_properties(self) -> Dict[str, Value]:
        return self.properties

    def set_default_property(self, v: Value):
        self.default_property = v

    def set_properties(self, properties: Dict[str, Value]):
        self.properties = properties

    def get_item(self, v: Value) -> Value:
        v = self.items.get(v)
        if v is None:
            v = self.default_item

    def set_item(self, k: Value, v: Value):
        if k
        # need to split k first
        ...

    def set_default_item(self, v: Value):
        self.default_item = v

    def get_default_item(self) -> Value:
        return self.default_item

    def get_scope_chain(self) -> ScopeChain:
        assert not self.scope_unknown, "Calling get_scope_chain when scope is unknown"
        return self.scope_chain

    def set_scope_chain(self, scope: ScopeChain):
        self.scope_chain = scope

    def add_to_scope_chain(self, new_scope: ScopeChain):
        ...

    def is_scope_chain_unknown(self) -> bool:
        return self.scope_unknown

    def __eq__(self, other):
        ...

    def remove(self, obj: 'Obj'):
        ...

    def replace_obj_label(self, old_label: ObjLabel, new_label: ObjLabel,
                          cache: Dict[ScopeChain, ScopeChain]):
        ...









class Old_Obj:
    cls: Optional[ObjLabel]

    scope_chain: Optional[ScopeChain]
    scope_chain_unknown: bool
    internal_value: 'Value'
    default_other_property: 'Value'
    writable: bool

    base_classes: Optional[List[ObjLabel]]
    self_scope: Scope

    @classmethod
    def make_the_none(cls) -> 'Obj':
        return ObjDefaults.the_none

    @classmethod
    def make_absent_modified(cls) -> 'Obj':
        return ObjDefaults.the_absent_modified

    @classmethod
    def make_the_none_modified(cls) -> 'Obj':
        return ObjDefaults.the_none_modified

    @classmethod
    def make_the_unknown(cls) -> 'Obj':
        return ObjDefaults.the_unknown

    def __init__(self, scope_chain: Optional[ScopeChain] = None,
                 cls: Optional[ObjLabel] = None,
                 base_classes: Optional[List[ObjLabel]] = None):
        self.scope_chain = scope_chain
        self.scope_chain_unknown = scope_chain is None
        self.cls = cls
        self.base_classes = base_classes

    @classmethod
    def make_cls(cls, scope_chain: ScopeChain) -> 'Obj':
        ret = cls()
        ...

    def is_cls(self):
        return self.cls is not None

    def get_cls(self) -> Optional[ObjLabel]:
        return self.cls

    def get_attr(self, var_name: str) -> Optional[Value]:
        return self.self_scope.get_var(var_name)

    def write_attr(self, var_name: str, value: Value) -> bool:
        return self.self_scope.update_var(var_name, value)

    def get_bases_label(self) -> List[ObjLabel]:
        assert self.is_cls and self.base_classes is not None, "Cannot get bases from Object!"
        return self.base_classes

    @classmethod
    def from_obj(cls, o: 'Obj') -> 'Obj':
        ret = cls(o.scope_chain, o.cls, o.base_classes)
        ...
        return ret

    def set_cls_label(self, cls: ObjLabel):
        self.cls = cls

    def get_cls_label(self) -> ObjLabel:
        return self.cls

    def is_writable(self) -> bool:
        return self.writable

    def is_unknown(self) -> bool:
        if self.scope_chain is None:
            return False
        for _, v in self.scope_chain.get_vars():
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
        if self.cls is not None and self.cls.get_kind() == LabelKind.Class:
            assert isinstance(self.cls.get_scope(), IRClass), "Type of class obj_label should be IRClass"
            return self.cls.get_scope()
        return None

    def maybe_class(self) -> bool:
        return

    def restrict_class(self) -> 'Cls':
        ...

    def set_scope_chain(self, sc: ScopeChain):
        self.scope_chain = sc
        self.scope_chain_unknown = False

    def set_scope_chain_unknown(self):
        self.scope_chain = None
        self.scope_chain_unknown = True

    def is_scope_chain_unknown(self) -> bool:
        return self.scope_chain_unknown

    def add_to_scope_chain(self, new_sc: ScopeChain):
        assert not self.scope_chain_unknown, "Calling add_to_scope_chain when scope is 'unknown'"
        res = self.scope_chain + new_sc
        changed = res == self.scope_chain
        self.scope_chain = res
        return changed

    def get_scope_chain(self) -> ScopeChain:
        return self.scope_chain

    def replace_non_modified_parts(self, other: 'Obj'):
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
