from typing import *
from .ir_scope import IRScope

__all__ = ["IRFunc"]


class IRFunc(IRScope):
    func_def: IRFuncDef
    scope: Optional[IRScope]

    def __init__(self, func_def, cfg=None, scope=None,
                 funcs=None, classes=None, imports=None):
        super().__init__(cfg, funcs, classes, imports)
        self.func_def = func_def
        self.scope = scope

    def set_scope(self, scope):
        self.scope = scope

    def get_name(self) -> str:
        return f'fn${self.func_def.name}'

    def __repr__(self) -> str:
        return ast.unparse(self.func_def.to_ast())
