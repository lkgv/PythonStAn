from typing import *

from pythonstan.graph.cfg import BaseBlock
from pythonstan.ir import IRScope
from .context import Context
from .call_edge import CallEdge
from ..solver_interface import SolverInterface


class CallGraph:
    # map from (callee entry, call context) to set of (caller node, caller context, edge context)
    call_sources: Dict[Tuple[BaseBlock, Context], Set[Tuple[BaseBlock, Context, Context]]]
    # map from (caller node, caller context) to (callee entry, edge context) to call edge.
    call_edge_info: Dict[Tuple[BaseBlock, Context], Dict[Tuple[BaseBlock, Context], CallEdge]]
    scope_entry_order: Dict[Tuple[BaseBlock, Context], int]
    context_order: Dict[Context, int]
    callees_ignoring_context: Dict[BaseBlock, Set[IRScope]]

    next_scope_entry_order: int = 0
    next_context_order: int = 0
    size_ignoring_contexts: int = 0

    def __init__(self, c: SolverInterface):
        self.call_sources = {}
        self.call_edge_info = {}
        self.scope_entry_order = {}
        self.context_order = {}
        self.callees_ignoring_context = {}
        self.c = c

    def add_target(self, caller: BaseBlock, caller_context: Context, callee: BaseBlock,
                   callee_scope: IRScope, edge_context: Context, edge: CallEdge) -> bool:
        changed: bool
        mb = self.call_edge_info.get((caller, caller_context), {})
        _to = (callee, edge_context)
        call_edge = mb.get(_to)
        _from = (edge.get_state().get_block(), edge.get_state().get_context())
        if call_edge is None:
            mb[_to] = edge.clone()
            changed = True
            callees_ci = self.callees_ignoring_context.get(caller)
            if callees_ci is None:
                callees_ci = {*()}
                self.callees_ignoring_context[caller] = callees_ci
            if callee_scope not in callees_ci:
                callees_ci.add(callee_scope)
                self.size_ignoring_contexts += 1
        else:
            changed = call_edge.get_state().propagate(edge.get_state(), True, False)
        return changed

    def add_source(self, caller: BaseBlock, caller_context: Context, callee: BaseBlock, callee_context: Context,
                   edge_context: Context):
        self.call_sources.setdefault((callee, callee_context), {*()})
        self.call_sources[(callee, callee_context)].add((caller, caller_context, edge_context))

    def register_scope_entry(self, bc: Tuple[BaseBlock, Context]):
        if bc not in self.scope_entry_order:
            self.scope_entry_order[bc] = self.next_scope_entry_order
            self.next_scope_entry_order += 1

    def get_scope_entry_order(self, bc: Tuple[BaseBlock, Context]) -> int:
        assert bc in self.scope_entry_order, f"Unexpected basic block and context {bc}"
        return self.scope_entry_order[bc]

    def get_context_order(self, c: Context) -> int:
        if c in self.context_order:
            return self.context_order[c]
        else:
            order = self.next_context_order
            self.context_order[c] = order
            self.next_scope_entry_order += 1
            return order

    def get_sources(self, bc: Tuple[BaseBlock, Context]) -> Set[Tuple[BaseBlock, Context, Context]]:
        return self.call_sources.get(bc, {*()})

    def get_call_edge(self, caller: BaseBlock, caller_context: Context, callee: BaseBlock, edge_context: Context) -> CallEdge:
        mb = self.get_call_edges(caller, caller_context)
        assert (callee, edge_context) in mb, "No such edge!"
        return mb[(callee, edge_context)]

    def get_call_edges(self, caller: BaseBlock, caller_context: Context) -> Dict[Tuple[BaseBlock, Context], CallEdge]:
        assert (caller, caller_context) in self.call_edge_info, "No such edge!"
        return self.call_edge_info[(caller, caller_context)]

    def get_size_ignoring_contexts(self) -> int:
        return self.size_ignoring_contexts

    def get_call_edge_info(self) -> Dict[Tuple[BaseBlock, Context], Dict[Tuple[BaseBlock, Context], CallEdge]]:
        return self.call_edge_info

    def get_reverse_edges_ignore_contexts(self) -> Dict[IRScope, Set[BaseBlock]]:
        m = {}
        for (b, ctx), v in self.call_sources.items():
            if self.is_ordinary_call_edge(b):
                scope = self.c.get_scope_from_base_block(b)
                m.setdefault(scope, {*()})
                for call_node, _, _ in v:
                    m[scope].add(call_node)
        return m

    def is_ordinary_call_edge(self, callee: BaseBlock) -> bool:
        ir_scope = self.c.get_scope_from_base_block(callee)
        scope_entry = self.c.get_graph().get_cfg(ir_scope).get_entry()
        return scope_entry == callee
