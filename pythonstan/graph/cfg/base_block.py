from typing import List, Optional, Set

from .statements import IRRStatement, Label
from ..graph import Node

__all__ = ["BaseBlock"]

S1 = 182239
S2 = 120721
S3 = 219943

class BaseBlock(Node):
    idx: int
    stmts: List[IRRStatement]

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

    def add(self, stmt: IRRStatement):
        self.stmts.append(stmt)

    def add_front(self, stmt: IRRStatement):
        if self.n_stmt() > 0 and isinstance(self.stmts[0], Label):
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
        stmts_str = '\\n'.join([str(s) for s in self.stmts])
        return '\\n'.join([head, stmts_str])

    def gen_label(self) -> Label:
        if self.n_stmt() > 0 and isinstance(self.stmts[0], Label):
            return self.stmts[0]
        else:
            label = Label(self.get_idx())
            self.add_front(label)
            return label

    def __hash__(self):
        return ((self.idx * S2) + S1) % S3
