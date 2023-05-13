from typing import List, Dict, Optional

from pythonstan.graph.cfg import IRScope
from pythonstan.utils.common import Singleton
from pythonstan.ir import IRModule
from .namespace import Namespace
from .classes import ClassManager
from .modules import ModuleManager
from .analysis_manager import AnalysisManager
from .scope_manager import ScopeManager
from .config import Config


class World(Singleton):
    analysis_manager: AnalysisManager
    cls_manager: ClassManager
    mod_manager: ModuleManager
    entrypoints: List[IRScope]
    exec_module_dag: Dict[IRScope, List[IRScope]]

    @classmethod
    def setup(cls):
        cls.cls_manager = ClassManager()
        cls.mod_manager = ModuleManager()
        cls.analysis_manager = AnalysisManager()
        cls.scope_manager = ScopeManager()
        cls.entrypoints = []

    def add_entry(self, scope: IRScope):
        self.entrypoints.append(scope)

    def get_entries(self) -> List[IRScope]:
        return self.entrypoints

    def get_namespace(self, namespace: Namespace) -> IRScope:
        ...

    def load_module(self, mod: IRModule):
        self.scope_manager.add_module(mod)
        tha_trans = self.analysis_manager.get_analyzer('ThreeAddressTransformer')
        mod_tha = tha_trans.analyze(mod)
        self.scope_manager.
        imports = ... # from tha_trans

        for imp in imports:
            self.load_module(mod)
        return module

    def build(self, config: Config):
        self.analysis_manager.build(config.get_analysis_list())
        self.scope_manager.build(config.filename, config.project_path)
