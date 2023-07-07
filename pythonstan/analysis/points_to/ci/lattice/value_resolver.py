from typing import Optional

from .state import State
from .obj import Obj
from .obj_label import ObjLabel, LabelKind
from .value import Value


class ValueResolver:
    def get_entry_state(self, s: State):
        return s.c.get_analysis_lattice_element().get_state(s.get_context(), s.get_block())

    def retrive_property(self, obj_label: ObjLabel, prop_name: str, s: State) -> Value:
        if obj_label.kind == LabelKind.Class:
            return self.retrive_cls_property(obj_label, prop_name, s)
        else:
            return self.retrive_obj_property(obj_label, prop_name, s)

    def retrive_obj_property(self, obj_label: ObjLabel, prop_name: str, s: State) -> Value:
        obj = s.get_obj(obj_label)
        prop = obj.get_property(prop_name)
        if prop is None:
            prop = self.optional_retrive_cls_property(obj.get_cls_label(), prop_name, s, True)
        return prop if prop is not None else Value.make_absent()

    def retrive_cls_property(self, obj_label: ObjLabel, prop_name: str, s: State) -> Value:
        prop = self.optional_retrive_cls_property(obj_label, prop_name, s, False)
        return prop if prop is not None else Value.make_absent()

    def optional_retrive_cls_property(self, obj_label: ObjLabel, prop_name: str, s: State,
                                      retrive_instance: bool = False) -> Optional[Value]:
        cls = s.get_obj(obj_label)
        assert obj_label.kind == LabelKind.Class and cls.is_cls(), "the obj to be retrived is not class"
        prop: Optional[Value] = None
        if retrive_instance:
            prop = cls.get_property(prop_name)
        if prop is None:
            prop = cls.get_class_property(prop_name)
        if prop is None:
            prop = cls.get_instance_property(prop_name)
        if prop is None:
            for base_label in cls.get_bases_label():
                prop = self.optional_retrive_cls_property(base_label, prop_name, s, retrive_instance)
                if prop is not None:
                    return prop
        return None

    def write_property(self, obj_label: ObjLabel, prop_name: str, value: Value, s: State):
        if obj_label.kind == LabelKind.Class:
            