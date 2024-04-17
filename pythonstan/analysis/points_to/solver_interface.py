from typing import Set, Dict, List, Optional

from .cs_manager import CSManager
from .heap_model import HeapModel
from .context import CSCallSite, CSScope
from .context_selector import ContextSelector
from pythonstan.world.class_hierarchy import ClassHierarchy
from pythonstan.graph.call_graph import CallEdge, CallKind
from .cs_call_graph import CSCallGraph
from .points_to_set import PointsToSet
from .pointer_flow_graph import PointerFlowGraph, FlowKind, EdgeTransfer
from ..analysis import AnalysisConfig
from pythonstan.ir import IRScope
from .stmts import StmtCollector, PtStmt
from .elements import Var , Pointer
from .work_list import Worklist


class SolverInterface:
    cs_manager: CSManager
    heap_model: HeapModel
    hierarchy: ClassHierarchy
    context_selector: ContextSelector
    call_graph: CSCallGraph
    pfg: PointerFlowGraph
    reachable_scopes: Set[IRScope]
    var2stmtcolle: Dict[Var, StmtCollector]
    scope2pt_ir: Dict[IRScope, List[PtStmt]]
    work_list: Worklist

    def __init__(self, config: AnalysisConfig, heap_model: HeapModel,
                 context_selector: ContextSelector, cs_manager: CSManager):
        self.config = config
        self.heap_model = heap_model
        self.context_selector = context_selector
        self.cs_manager = cs_manager
        self.work_list = Worklist()

        from pythonstan.world.world import World
        self.hierarchy = World().class_hierarchy
        self.scope_manager = World().scope_manager

        self.var2stmtcolle = {}
        self.scope2pt_ir = {}

    def add_call_edge(self, call_edge: CallEdge[CSCallSite, CSScope]):
        self.work_list.add_edge(call_edge)

    def add_pfg_edge(self, src: Pointer, tgt: Pointer, kind: FlowKind,
                     transfer: Optional[EdgeTransfer] = None):
        if not self.pfg.has_edge(kind, src, tgt):
            edge = self.pfg.add_edge(kind, src, tgt)
            if transfer is not None and edge.:
                ...


    def add_points_to(self, pointer: Pointer, pts: PointsToSet):
        self.work_list.add_pts(pointer, pts)


    def add_var_points_to(self):
        ...

    def get_call_kind(self, stmt: PtInvoke) -> CallKind:
        ...