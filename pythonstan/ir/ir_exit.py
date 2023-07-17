from .ir_statement import IRAbstractStmt
from .ir_scope import IRScope


class IRExit(IRAbstractStmt):
    scope: IRScope

    def __init__(self, scope: IRScope):
        self.scope = scope

    def get_scope(self) -> IRScope:
        return self.scope
