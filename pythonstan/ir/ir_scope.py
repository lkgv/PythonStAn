from typing import *
import ast
from abc import ABC, abstractmethod

from .ir_statement import IRStatement
from pythonstan.utils.persistent_rb_tree import PersistentMap
from pythonstan.graph.cfg import ControlFlowGraph

__all__ = ["IRScope"]


class IRScope(ABC):
    defs: PersistentMap[str, IRStatement]
    cfg: Optional[ControlFlowGraph]

    @abstractmethod
    def __init__(self, cfg, defs=None):
        self.cfg = cfg
        if defs is not None:
            self.defs = defs
        else:
            self.defs = PersistentMap()

    def set_cfg(self, cfg: ControlFlowGraph):
        self.cfg = cfg

    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError
