from typing import List, Dict

from pythonstan.graph.cfg import IRScope
from pythonstan.utils.common import Singleton
from .namespace import Namespace
from .classes import ClassManager
from .modules import ModuleManager


class World(Singleton):
    cls_manager: ClassManager = ClassManager()
    mod_manager: ModuleManager = ModuleManager()
    entrypoints: List[IRScope] = []
    exec_module_dag: Dict[IRScope, List[IRScope]]
    entry_module: IRScope

    @classmethod
    def reset(cls):
        cls.cls_manager = ClassManager()
        cls.mod_manager = ModuleManager()
        cls.entrypoints = []

    def add_entry(self, scope: IRScope):
        self.entrypoints.append(scope)

    def get_namespace(self, namespace: Namespace) -> IRScope:
        ...
