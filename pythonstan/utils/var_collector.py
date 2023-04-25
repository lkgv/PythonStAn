from ast import NodeVisitor, expr_context, Store, Load, Del, Name
from typing import Dict, Tuple


class VarCollector(NodeVisitor):
    var_map: Dict[str, int]
    ctx: Tuple[expr_context]
    next_idx: int

    def __init__(self, *keys, ctx="", **kwargs):
        super().__init__(*keys, **kwargs)
        self.reset(ctx)

    def reset(self, ctx: str=""):
        self.next_idx = 0
        self.var_map = {}
        if ctx == 'store':
            self.ctx = (Store,)
        elif ctx == 'no_store':
            self.ctx = (Load, Del)
        elif ctx == 'load':
            self.ctx = (Load,)
        elif ctx == 'del':
            self.ctx = (Del,)
        else:
            self.ctx = (Store, Load, Del)
    
    def find(self, name):
        if isinstance(name, Name):
            return self.var_map.get(name.id, default=None)
        if isinstance(name, str):
            return self.var_map.get(name, default=None)
        return None
    
    def get_vars(self):
        return set(self.var_map.keys())
    
    def size(self):
        return len(self.var_map)
    
    def visit_Name(self, node):
        if isinstance(node.ctx, self.ctx):
            if node.id not in self.var_map:
                self.var_map[node.id] = self.next_idx
                self.next_idx += 1
        self.generic_visit(node)
