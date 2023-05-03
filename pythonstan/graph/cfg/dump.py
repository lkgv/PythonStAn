import ast
from queue import Queue

from .models import ControlFlowGraph, BaseBlock, Label

def dump(cfg: ControlFlowGraph):
    entry = cfg.get_entry()
    stmts = []
    visited = {*()}
    q = Queue()
    q.put(entry)
    while not q.empty():
        cur = q.get()
        if cfg.in_degree_of(cur) > 1:
            lab = Label(cur)
            stmts.append(lab)
        for stmt in cur.stmts:
            stmts.append(stmt)
        for v in cfg.succs_of(cur):
            if v not in visited:
                visited.add(v)
                q.put(v)
    return stmts
