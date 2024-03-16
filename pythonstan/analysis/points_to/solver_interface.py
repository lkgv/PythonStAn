from typing import Set, Dict, List

from .cs_manager import CSManager
from .heap_model import HeapModel
from .context_selector import ContextSelector
from pythonstan.world.class_hierarchy import ClassHierarchy
from .cs_call_graph import CSCallGraph
from .pointer_flow_graph import PointerFlowGraph
from ..analysis import AnalysisConfig
from pythonstan.ir import IRScope
from .stmts import StmtCollector, PtStmt
from .elements import Var


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

    def __init__(self, config: AnalysisConfig, heap_model: HeapModel,
                 context_selector: ContextSelector, cs_manager: CSManager):
        self.config = config
        self.heap_model = heap_model
        self.context_selector = context_selector
        self.cs_manager = cs_manager

        from pythonstan.world.world import World
        self.hierarchy = World().class_hierarchy

        self.var2stmtcolle = {}
        self.scope2pt_ir = {}
