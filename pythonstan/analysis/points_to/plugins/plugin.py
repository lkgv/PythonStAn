from abc import ABC
from ..cs_manager import Context, CSVar, CSScope, CSObj, CSCallSite
from ..points_to_set import PointsToSet
from ..cs_call_graph import CSCallEdge
from ..stmts import PtStmt, PtInvoke
from pythonstan.ir import IRScope
from pythonstan.graph.call_graph import CallKind


class Plugin(ABC):
    def on_start(self):
        ...

    def on_finish(self):
        ...

    def on_new_pts(self, cs_var: CSVar, pts: PointsToSet):
        ...

    def on_new_call_edge(self, edge: CSCallEdge):
        ...

    def on_new_scope(self, scope: IRScope):
        ...

    def on_new_stmt(self, stmt: PtStmt, container: IRScope):
        ...

    def on_new_cs_scope(self, cs_scope: CSScope):
        ...

    def on_new_invoke(self, kind: CallKind, cs_call_site: CSCallSite, cs_callee: CSScope):
        ...

    def on_unresolved_call(self, recv: CSObj, context: Context, invoke: PtInvoke):
        ...
