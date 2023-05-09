from typing import *
from abc import ABC, abstractmethod

from .ir_statements import *
from pythonstan.graph.cfg import ControlFlowGraph

__all__ = ["IRScope"]


class IRScope(ABC):
    cfg: Optional['ControlFlowGraph']
    prev_scope: Optional['IRScope']

    @abstractmethod
    def __init__(self, cfg, prev_scope):
        self.set_cfg(cfg)
        self.prev_scope = prev_scope

    def set_cfg(self, cfg: ControlFlowGraph):
        self.cfg = cfg

    def set_prev_scope(self, scope: 'IRScope'):
        self.prev_scope = scope

    @abstractmethod
    def get_name(self) -> str:
        raise NotImplementedError
