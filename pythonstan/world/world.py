from typing import List, Dict, Optional

from pythonstan.graph.cfg import IRScope
from pythonstan.utils.common import Singleton
from pythonstan.ir import IRModule
from .namespace import Namespace
from .classes import ClassManager
from .modules import ModuleManager
from .analysis_manager import AnalysisManager
from .scope_manager import ScopeManager


class World(Singleton):
    analysis_manager: AnalysisManager
    cls_manager: ClassManager = ClassManager()
    mod_manager: ModuleManager = ModuleManager()
    entrypoints: List[IRScope] = []
    exec_module_dag: Dict[IRScope, List[IRScope]]
    entry_module: Optional[IRScope] = None

    @classmethod
    def reset(cls):
        cls.cls_manager = ClassManager()
        cls.mod_manager = ModuleManager()
        cls.analysis_manager = AnalysisManager()
        cls.scope_manager = ScopeManager()
        cls.entrypoints = []

    def add_entry(self, scope: IRScope):
        self.entrypoints.append(scope)

    def get_namespace(self, namespace: Namespace) -> IRScope:
        ...

    def load_module(self, filename) -> IRModule:
        namespace = Namespace.from_str(filename)
        module = IRModule.load_module(namespace.module_name(), filename)
        # ...
        return module
