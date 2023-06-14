from typing import Dict, List
from pythonstan.ir import IRClass


class ClassHierarchy:
    bases: Dict[IRClass, List[IRClass]]
    subclasses: Dict[IRClass, List[IRClass]]

    def is_subclass(self, cls: IRClass, sub_cls: IRClass) -> bool:
        if cls in self.subclasses and sub_cls in self.subclasses[cls]:
            return True
        else:
            return False

    def add_subclass(self, cls: IRClass, sub_cls: IRClass):
        if sub_cls not in self.subclasses[cls]:
            self._add_item(self.bases, sub_cls, cls)
            self._add_item(self.subclasses, cls, sub_cls)

    @staticmethod
    def _add_item(kv_map, key, value):
        if key in kv_map:
            kv_map[key].append(value)
        else:
            kv_map[key] = [value]
