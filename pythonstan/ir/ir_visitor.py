from abc import ABC
from .ir_statement import IRStatement

__all__ = ['IRVisitor']


class IRVisitor(ABC):
    def visit(self, ir: IRStatement):
        cls = ir.__class__.__name__.
        m_name = f"visit_{cls}"
        if hasattr(self, m_name):
            m = getattr(self, m_name)
            m(ir)
        else:
            self.visit_default(ir)

    def visit_default(self, ir: IRStatement):
        pass
