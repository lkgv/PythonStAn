from typing import Set

from .work_list import Worklist
from .cs_manager import CSManager
from .heap_model import HeapModel
from .context_selector import ContextSelector
from .context import CSVar
from pythonstan.world.class_hierarchy import ClassHierarchy
from .cs_call_graph import CSCallGraph, CallEdge
from .pointer_flow_graph import PointerFlowGraph
from .points_to_set import PointsToSet
from .elements import Pointer
from ..analysis import AnalysisConfig
from pythonstan.ir import IRScope
from .plugin import Plugin


class StmtProcessor:
    ...

class Solver:
    cs_manager: CSManager
    heap_model: HeapModel
    hierarchy: ClassHierarchy
    context_selector: ContextSelector
    work_list: Worklist
    call_graph: CSCallGraph
    pfg: PointerFlowGraph
    reachable_scopes: Set[IRScope]
    plugin: Plugin

    def __init__(self, config: AnalysisConfig, heap_model: HeapModel,
                 context_selector: ContextSelector, cs_manager: CSManager):
        self.config = config
        self.heap_model = heap_model
        self.context_selector = context_selector
        self.cs_manager = cs_manager

        from pythonstan.world.world import World
        self.hierarchy = World().class_hierarchy

    def get_points_to_set_of(self, pointer: Pointer) -> PointsToSet:
        pts = pointer.get_points_to_set()
        if pts is None:
            pts = PointsToSet()
            pointer.set_points_to_set(pts)
        return pts

    def set_plugin(self, plugin: Plugin):
        self.plugin = plugin

    def solve(self):
        self.initialize()
        self.analyze()

    def initialize(self):
        self.call_graph = CSCallGraph(self.cs_manager)
        self.pfg = PointerFlowGraph()
        self.work_list = Worklist()
        self.reachable_scopes = set()
        self.plugin.on_start()


    def analyze(self):
        while not self.work_list.is_empty():
            entry = self.work_list.get()
            if entry is None:
                break
            elif isinstance(entry, CallEdge):
                self.process_call_edge(entry)
            elif isinstance(entry, tuple):
                p, pts = entry
                diff = self.propagate(p, pts)
                if not diff.is_empty() and isinstance(p, CSVar):
                    self.process_instance_store(p, diff)
                    self.process_instance_load(p, diff)
                    self.process_call(p, diff)
                    self.plugin.on_new_pts(p, diff)
        self.plugin.on_finish()

    def propagate(self, pointer: Pointer, pts: PointsToSet) -> PointsToSet:
        ...

    # logic ends

    def add_cs_method(self):
        ...

    def add_stmts(self):
        ...

    def init_class(self):
        ...

    def add_pdg_edge(self):
        ...

    def add_points_to(self):
        ...

    def add_entry_point(self):
        ...

    def add_var_points_to(self):
        ...
