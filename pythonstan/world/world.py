from typing import List, Dict, Optional, Tuple

from pythonstan.utils.common import Singleton
from pythonstan.ir import IRModule, IRScope, IRImport
from .namespace import Namespace, NamespaceManager
from .scope_manager import ScopeManager
from .config import Config
from .class_hierarchy import ClassHierarchy
from .import_manager import ImportManager


class World(Singleton):
    namespace_manager: NamespaceManager
    entry_module: IRModule
    class_hierarchy: ClassHierarchy
    import_manager: ImportManager
    module2ns: Dict[IRModule, Namespace]

    @classmethod
    def setup(cls):
        cls.scope_manager = ScopeManager()
        cls.namespace_manager = NamespaceManager()
        cls.class_hierarchy = ClassHierarchy()
        cls.import_manager = ImportManager()

        cls.module2ns = {}

    def build(self, config: Config):
        self.scope_manager.build()
        self.import_manager.build()
        self.namespace_manager.build(config.project_path, config.library_paths)

    def set_entry_module(self, module: IRModule):
        self.entry_module = module

    def get_entry_module(self) -> IRModule:
        return self.entry_module
