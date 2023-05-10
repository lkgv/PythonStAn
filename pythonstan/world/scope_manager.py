from typing import Set, List, Dict

from .namespace import Namespace
from pythonstan.ir.ir_scope import IRScope
from pythonstan.ir.ir_func import IRFunc
from pythonstan.ir.ir_class import IRClass
from pythonstan.utils.persistent_rb_tree import PersistentMap


class ScopeManager:
    scopes: Set[IRScope]
    subscopes: Dict[IRScope, List[IRScope]]
    father: Dict[IRScope, IRScope]
    ns2scope: Dict[Namespace, IRScope]
