from typing import *
from .ir_scope import IRScope

__all__ = ["IRFunc"]


class IRFunc(IRScope):
    func_def: IRFuncDef

    def __init__(self, func_def, cfg=None, prev_scope=None):
        super().__init__(cfg, prev_scope)
        self.func_def = func_def

    def get_name(self) -> str:
        return f'fn${self.func_def.name}'

    def __repr__(self) -> str:
        return ast.unparse(self.func_def.to_ast())
