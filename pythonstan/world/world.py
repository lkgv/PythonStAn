from typing import List, Dict, Optional, Tuple

from pythonstan.utils.common import Singleton
from pythonstan.ir import IRModule, IRScope
from .namespace import Namespace, NamespaceManager
from .scope_manager import ScopeManager
from .config import Config


class World(Singleton):
    namespace_manager: NamespaceManager
    entry_module: IRModule

    @classmethod
    def setup(cls):
        cls.scope_manager = ScopeManager()
        cls.namespace_manager = NamespaceManager()

    def build(self, config: Config):
        self.scope_manager.build()
        self.namespace_manager.build(config.project_path, config.library_paths)

    def set_entry_module(self, module: IRModule):
        self.entry_module = module

    def get_entry_module(self) -> IRModule:
        return self.entry_module
