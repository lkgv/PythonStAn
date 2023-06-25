from typing import List, Optional, Set

from pythonstan.ir import IRStatement, Label, IRPass
from ..graph import Node

__all__ = ["BaseBlock"]


class BaseBlock(Node):
    idx: int
    only_pass: bool
    stmts: List[IRStatement]

    def __init__(self, idx: int = -1, stmts: List[IRStatement] = None):
        self.idx = idx
        if stmts is None or len(stmts) == 0:
            self.stmts = [IRPass()]
            self.only_pass = True
        elif len(stmts) == 1 and isinstance(stmts[0], Label):
            self.stmts = stmts + [IRPass()]
            self.only_pass = True
        else:
            self.stmts = [x for x in stmts]
            self.only_pass = False

    def get_stmts(self):
        return self.stmts

    def get_idx(self):
        return self.idx

    def get_name(self) -> str:
        return f"[{self.idx}]"

    def check_only_pass(self):
        if self.only_pass:
            for stmt in self.stmts:
                if isinstance(stmt, IRPass):
                    self.stmts.remove(stmt)
            self.only_pass = False

    def add(self, stmt: IRStatement):
        if not isinstance(stmt, Label):
            self.check_only_pass()
        self.stmts.append(stmt)

    def add_front(self, stmt: IRStatement):
        if not isinstance(stmt, Label):
            self.check_only_pass()
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
