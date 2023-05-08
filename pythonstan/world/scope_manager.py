from typing import Set, List, Dict

from pythonstan.ir.ir_scope import IRScope
from pythonstan.ir.ir_func import IRFunc
from pythonstan.ir.ir_class import IRClass
from pythonstan.utils.persistent_rb_tree import PersistentMap


class ScopeManager:
    scopes: Set[IRScope]
    class_map: PersistentMap[IRScope, IRClass]
    func_map: PersistentMap[IRScope, IRFunc]

    def add_class