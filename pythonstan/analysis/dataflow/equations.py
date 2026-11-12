"""The finite mathematical operations consumed by the worklist solver.

The solver does not need registry membership or a driver. Both an ordinary
analysis and the integer encoding of liveness implement these same equations.
"""

from typing import Protocol

from pythonstan.graph.cfg import BaseBlock, CFGEdge, ControlFlowGraph


class DataflowEquations[Fact](Protocol):
    @property
    def is_forward(self) -> bool: ...

    @property
    def cfg(self) -> ControlFlowGraph: ...

    def get_cfg(self) -> ControlFlowGraph: ...

    def new_boundary_fact(self) -> Fact: ...

    def new_init_fact(self) -> Fact: ...

    def meet(self, first: Fact, second: Fact, /) -> Fact: ...

    def transfer_node(self, node: BaseBlock, fact: Fact) -> Fact: ...

    def need_transfer_edge(self, edge: CFGEdge) -> bool: ...

    def transfer_edge(self, edge: CFGEdge, fact: Fact) -> Fact: ...
