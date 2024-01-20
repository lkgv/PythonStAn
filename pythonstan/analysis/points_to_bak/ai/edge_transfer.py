from abc import ABC

from pythonstan.graph.cfg.edges import *


class EdgeVisitor(ABC):
    def visit(self, ir: CFGEdge):
        cls = ir.__class__.__name__
        m_name = f"visit_{cls}"
        if hasattr(self, m_name):
            m = getattr(self, m_name)
            m(ir)
        else:
            self.visit_default(ir)

    def visit_default(self, ir: CFGEdge):
        pass


class EdgeTransfer(EdgeVisitor):

    def visit_NormalEdge(self, state, edge):
        return state

    def visit_IfEdge(self, state, edge: IfEdge):
        if edge.value is True:
            edge.test
        return state
