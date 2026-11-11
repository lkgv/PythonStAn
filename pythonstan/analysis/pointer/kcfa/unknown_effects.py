"""Persistent unknown-call effects; diagnostic ledgers are separate consumers."""
from .context import Ctx
from .variable import Variable, VariableKind
from .points_to_set import PointsToSet
from .pointer_flow_graph import NormalNode


class UnknownEffects:
    """An unknown Python call may affect reachable heap, globals and cells.

    The initial conservative profile havocs all known heap/captured cells and
    source globals, including fields discovered later. It publishes wildcard
    callback/exception events. It never treats arbitrary callbacks as a closed
    concrete call graph or certifies their ordering.
    """
    def __init__(self, state):
        self.state = state
        self.source = None
        self.origins = set()
        self.events = []
        self.subscriptions = set()

    def install(self, scope, top_object, origin):
        if origin in self.origins:
            return
        self.origins.add(origin)
        if self.source is None:
            self.source = Ctx(scope.context, scope,
                              Variable("$unknown_effect_havoc", VariableKind.TEMPORARY))
            self.state._worklist.add((scope, NormalNode(self.source),
                                      PointsToSet.singleton(top_object)))
        for obj, field in tuple(self.state._field_accesses):
            access = self.state._field_accesses[(obj, field)]
            self.subscribe(Ctx(obj.context, None, access))
        for registers in tuple(self.state._heap.heap.values()):
            for variable in tuple(registers.values()):
                self.variable_created(variable)
        for captures in (self.state._heap.cell_vars, self.state._heap.nonlocal_vars,
                         self.state._heap.global_vars):
            for variables in tuple(captures.values()):
                self.captures_created(variables)
        self.events.extend((
            {"kind":"heap_havoc", "origin":origin, "scope":"all_known_and_future_fields"},
            {"kind":"unknown_callback", "origin":origin, "target":"*", "order":"unknown"},
            {"kind":"unknown_exception", "origin":origin, "exception":"BaseException", "order":"unknown"},
        ))

    def subscribe(self, variable):
        if self.source is None or variable == self.source or variable in self.subscriptions:
            return
        self.subscriptions.add(variable)
        self.state._add_var_points_flow(self.source, variable)

    def field_created(self, obj, field_access):
        self.subscribe(Ctx(obj.context, None, field_access))

    def variable_created(self, variable):
        value = variable.content
        if (value.kind in {VariableKind.GLOBAL, VariableKind.CELL, VariableKind.NONLOCAL}
                and not value.name.startswith(("$", "<"))):
            self.subscribe(variable)

    def captures_created(self, variables):
        for variable in tuple(variables.values()):
            # A captured cell can be implemented by a PARAMETER/LOCAL slot.
            self.subscribe(variable)

    def snapshot(self):
        return tuple(dict(event) for event in self.events)
