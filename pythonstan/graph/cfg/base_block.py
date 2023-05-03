from typing import List, Optional, Set

from .statements import CFGStmt, Label
from ..graph import Node

__all__ = ["BaseBlock"]


class BaseBlock(Node):
    idx: int
    stmts: List[CFGStmt]

    def __init__(self, idx=-1, stmts=None):
        self.idx = idx
        if stmts is None:
            self.stmts = []
        else:
            self.stmts = [x for x in stmts]

    def get_stmts(self):
        return self.stmts

    def get_idx(self):
        return self.idx

    def get_name(self) -> str:
        return f"[{self.idx}]"

    def add(self, stmt: CFGStmt):
        self.stmts.append(stmt)

    def add_front(self, stmt: CFGStmt):
        if isinstance(self.stmts[0], Label):
            self.stmts.insert(1, stmt)
        else:
            self.stmts.insert(0, stmt)

    def n_stmt(self) -> int:
        return len(self.stmts)

    def get_stores(self) -> Set[str]:
        stores = {*()}
        for stmt in self.stmts:
            stores.update(stmt.get_stores())
        return stores

    def get_loads(self) -> Set[str]:
        loads = {*()}
        for stmt in self.stmts:
            loads.update(stmt.get_loads())
        return loads

    def get_dels(self) -> Set[str]:
        dels = {*()}
        for stmt in self.stmts:
            dels.update(stmt.get_dels())
        return dels

    def __str__(self):
        head = self.get_name()
        stmts_str = '\\n'.join(self.stmts)
        return '\\n'.join([head, stmts_str])
