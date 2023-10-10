from abc import ABC

from pythonstan.graph.cfg.edges import *
from .lattice.state import State


class EdgeVisitor(ABC):
    def visit(self, state, e: CFGEdge):
        cls = e.__class__.__name__
        m_name = f"visit_{cls}"
        if hasattr(self, m_name):
            m = getattr(self, m_name)
            m(state, e)
        else:
            self.visit_default(state, e)

    def visit_default(self, state, e: CFGEdge):
        pass


class EdgeTransfer(EdgeVisitor):

    def visit_NormalEdge(self, state: State, edge):
        return state.get_context()

    def visit_IfEdge(self, state, edge):
        ...
