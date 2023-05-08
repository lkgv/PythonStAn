from typing import List

from pythonstan.graph.cfg import IRScope
from pythonstan.utils.common import Singleton
from .classes import ClassManager
from .modules import ModuleManager


class World(Singleton):
    cls_manager: ClassManager = ClassManager()
    mod_manager: ModuleManager = ModuleManager()
    entrypoints: List[IRScope] = []

    @classmethod
    def reset(cls):
        cls.cls_manager = ClassManager()
        cls.mod_manager = ModuleManager()
        cls.entrypoints = []

    def add_entry(self, scope: IRScope):
        self.entrypoints.append(scope)

