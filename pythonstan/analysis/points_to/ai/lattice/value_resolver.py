from typing import Optional, Set, Tuple

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
            prop = self.retrive_obj_property(obj_label, prop_name, s)
            return self.retrive_cls_property(obj_label, prop_name, s) if prop is None else prop

    def retrive_obj_properties(self, obj_label: ObjLabel, s: State) -> Set[Tuple[str, Value]]:
        obj = s.get_obj(obj_label)
        return set(obj.self_scope.var_map.items())

    def retrive_obj_property(self, obj_label: ObjLabel, prop_name: str, s: State) -> Optional[Value]:
        obj = s.get_obj(obj_label)
        return obj.get_attr(prop_name)

    def retrive_cls_property(self, obj_label: ObjLabel, prop_name: str, s: State) -> Value:
        prop = self.optional_retrive_cls_property(obj_label, prop_name, s, False)
        return prop if prop is not None else Value.make_absent()

    def optional_retrive_cls_property(self, obj_label: ObjLabel, prop_name: str, s: State) -> Optional[Value]:
        cls = s.get_obj(obj_label)
        assert obj_label.kind == LabelKind.Class and cls.is_cls(), "the obj to be retrived is not class"
        prop: Optional[Value] = cls.get_attr(prop_name)
        if prop is None:
            for base_label in cls.get_bases_label():
                prop = self.optional_retrive_cls_property(base_label, prop_name, s)
                if prop is not None:
                    return prop
        return None

    def write_property(self, obj_label: ObjLabel, prop_name: str, value: Value, s: State):
        if obj_label.kind == LabelKind.Class:
            return self.write_cls_property(obj_label, prop_name, value, s)
        else:
            return self.write_obj_property(obj_label, prop_name, value, s)

    def write_obj_property(self, obj_label: ObjLabel, prop_name: str, value: Value, s: State):
        obj = s.get_obj(obj_label)
        obj.write_attr(prop_name, value)

    def write_cls_property(self, obj_label: ObjLabel, prop_name: str, value: Value, s: State):
        cls = s.get_obj(obj_label)
        cls.write_attr(prop_name, value)

    def get_var(self, var_name: str, s: State) -> Optional[Value]:
        return s.get_execution_context().get_var(var_name)

    def set_var(self, var_name: str, value: Value, s: State) -> bool:
        return s.get_execution_context().set_var(var_name, value)

