from typing import Set, List, Dict, Any

from pythonstan.ir.ir_scope import IRScope
from pythonstan.ir.ir_func import IRFunc
from pythonstan.ir.ir_class import IRClass
from pythonstan.utils.persistent_rb_tree import PersistentMap


class ScopeManager:
    scopes: Set[IRScope]
    class_map: Dict[IRScope, PersistentMap[str, IRClass]]
    func_map: Dict[IRScope, PersistentMap[str, IRFunc]]
    def_map: Dict[IRScope, PersistentMap[str, Any]]

    def __init__(self):
        self.scopes = {*()}
        self.class_map = {}
        self.func_map = {}
        self.def_map = {}

    def add_scope(self, scope: IRScope):
        if scope not in self.scopes:
            self.scopes.add(scope)
            self.class_map[scope] = PersistentMap()
            self.func_map[scope] = PersistentMap()
            self.def_map[scope] = PersistentMap()

    def set_class_map(self, scope: IRScope, class_map, backup=True):
        if backup:
            self.class_map[scope].recover(class_map.backup())
        else:
            self.class_map[scope] = class_map

    def set_func_map(self, scope: IRScope, func_map, backup=True):
        if backup:
            self.func_map[scope].recover(func_map.backup())
        else:
            self.func_map[scope] = func_map

    def set_def_map(self, scope: IRScope, def_map, backup=True):
        if backup:
            self.def_map[scope].recover(def_map.backup())
        else:
            self.def_map[scope] = def_map

    def add_class(self, scope: IRScope, name: str, cls: IRClass):
        self.class_map[scope].set(name, cls)

    def add_func(self, scope: IRScope, name: str, func: IRFunc):
        self.func_map[scope].set(name, func)

    def add_def(self, scope: IRScope, name: str, defi: Any):
        self.def_map[scope].set(name, defi)
