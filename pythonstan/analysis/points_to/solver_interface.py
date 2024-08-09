from typing import Set, Dict, List, Optional

from .cs_manager import CSManager
from .heap_model import HeapModel
from .context import Context
from .context_selector import ContextSelector
from pythonstan.world.class_hierarchy import ClassHierarchy
from pythonstan.graph.call_graph import CallEdge, CallKind
from .cs_call_graph import CSCallGraph, CSCallEdge
from .pointer_flow_graph import PointerFlowGraph, FlowKind, EdgeTransfer
from ..analysis import AnalysisConfig
from pythonstan.ir import IRScope
from .elements import *
from .work_list import Worklist


class SolverInterface:
    from pythonstan.world.world import World

    cs_manager: CSManager
    heap_model: HeapModel
    context_selector: ContextSelector
    call_graph: CSCallGraph
    pfg: PointerFlowGraph
    reachable_scopes: Set[IRScope]
    work_list: Worklist

    def __init__(self, config: AnalysisConfig, heap_model: HeapModel,
                 context_selector: ContextSelector, cs_manager: CSManager):
        self.config = config
        self.heap_model = heap_model
        self.context_selector = context_selector
        self.cs_manager = cs_manager
        self.work_list = Worklist()
        self.hierarchy = self.get_world().class_hierarchy
        self.scope_manager = self.get_world().scope_manager

    def get_pts_of(self, pointer: Pointer) -> PointsToSet:
        pts = pointer.get_points_to_set()
        if pts is None:
            pts = PointsToSet()
            pointer.set_points_to_set(pts)
        return pts

    def get_return_var(self, frame: PtFrame) -> Var:
        return frame.get_var_write('__return_var__')

    def get_yield_var(self, frame: PtFrame) -> Var:
        return frame.get_var_write('__yield_var__')

    def get_world(self) -> World:
        from pythonstan.world.world import World
        return World()

    def get_property(self, obj: Obj, property: str, writable: bool = False) -> Optional[InstanceField]:
        if writable:
            return self.get_property_writable(obj, property)
        if isinstance(obj, ClassObj):
            return self.get_class_property(obj, property)
        elif isinstance(obj, InstanceObj):
            return self.get_instance_property(obj, property)
        else:
            raise NotImplementedError()

    def new_property(self, obj: Obj, property: str):
        field = InstanceField(obj, property)
        obj.set_property(property, field)

    def get_property_writable(self, obj: Obj, property: str) -> InstanceField:
        field = obj.get_property(property)
        if field is None:
            self.new_property(obj, property)
            field = obj.get_property(property)
        return field

    def get_class_property(self, obj: ClassObj, property: str) -> Optional[InstanceField]:
        # TODO a overapproximation: just selected one obj from the PTS of one parent in the parent list.
        #      It should be either a set or a large amount of propagated objs (considering subclasses and their instances).

        prop = obj.get_property(property)
        if prop is not None:
            return prop
        for parent in obj.get_parents():
            par_objs = parent.get_points_to_set()
            for par_obj in par_objs:
                prop = self.get_class_property(par_obj, property)
                if prop is not None:
                    return prop
        return None

    def get_instance_property(self, obj: InstanceObj, property: str) -> Optional[InstanceField]:
        prop = obj.get_property(property)
        if prop is not None:
            return prop
        cls_prop = self.get_class_property(obj.get_type(), property)
        if cls_prop is not None:
            return cls_prop
        return None


    def add_call_edge(self, call_edge: CSCallEdge, frame: PtFrame):
        self.work_list.add_edge((call_edge, frame))

    def add_pfg_edge(self, src: Pointer, tgt: Pointer, kind: FlowKind,
                     transfer: Optional[EdgeTransfer] = None):
        if not self.pfg.has_edge(kind, src, tgt):
            edge = self.pfg.add_edge(kind, src, tgt)
            if transfer is not None and edge.add_transfer(transfer):
                target_set = transfer.apply(edge, self.get_pts_of(src))
                if not target_set.is_empty():
                    self.work_list.add_pts(tgt, target_set)

    def add_points_to_pts(self, pointer: Pointer, pts: PointsToSet):
        self.work_list.add_pts(pointer, pts)

    def add_points_to_obj(self, pointer: Pointer, obj: Obj):
        self.add_points_to_pts(pointer, PointsToSet.from_obj(obj))

    def get_call_kind(self, stmt: PtInvoke) -> CallKind:
        return stmt.get_call_kind()
