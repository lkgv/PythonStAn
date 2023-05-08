from typing import *
from .ir_scope import IRScope

__all__ = ["IRClass"]

class IRClass(IRScope):
    class_def: IRClassDef
    scope: Optional[IRScope]

    def __init__(self, class_def, cfg=None, scope=None,
                 funcs=None, classes=None, imports=None):
        super().__init__(cfg, funcs, classes, imports)
        self.class_def = class_def
        self.scope = scope

    def set_scope(self, scope):
        self.scope = scope

    def get_name(self) -> str:
        return f'cls${self.class_def.name}'

    def __repr__(self) -> str:
        return str(self.class_def)
