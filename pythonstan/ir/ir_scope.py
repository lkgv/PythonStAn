from typing import *
import ast
from abc import ABC, abstractmethod

from .ir_statement import IRStatement
from pythonstan.utils.persistent_rb_tree import PersistentMap
from pythonstan.graph.cfg import ControlFlowGraph

__all__ = ["IRScope"]


class IRScope(ABC):
    qualname: str

    @abstractmethod
    def __init__(self, qualname: str):
        self.qualname = qualname

    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError

    def get_qualname(self) -> str:
        return self.qualname

