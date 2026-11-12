"""Persistent unknown-call effects; diagnostic ledgers are separate consumers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.json_data import JsonObject

from .context import Ctx, Scope
from .object import AbstractObject
from .variable import FieldAccess

if TYPE_CHECKING:
    from .state import PointerAnalysisState

from .pointer_flow_graph import NormalNode
from .points_to_set import PointsToSet
from .variable import Variable, VariableKind


class UnknownEffects:
    """An unknown Python call may affect reachable heap, globals and cells.

    The initial conservative profile havocs all known heap/captured cells and
    source globals, including fields discovered later. It publishes wildcard
    callback/exception events. It never treats arbitrary callbacks as a closed
    concrete call graph or certifies their ordering.
    """

    def __init__(self, state: PointerAnalysisState) -> None:
        self.state: PointerAnalysisState = state
        self.source: Ctx[Variable] | None = None
        self.origins: set[str] = set()
        self.events: list[JsonObject] = []
        self.subscriptions: set[Ctx[Variable | FieldAccess]] = set()

    def install(self, scope: Scope, top_object: AbstractObject, origin: str) -> None:
        if origin in self.origins:
            return
        self.origins.add(origin)
        if self.source is None:
            self.source = Ctx(
                scope.context, scope, Variable("$unknown_effect_havoc", VariableKind.TEMPORARY)
            )
            self.state._worklist.add(
                (scope, NormalNode(self.source), PointsToSet.singleton(top_object))
            )
        for obj, field in tuple(self.state._field_accesses):
            access = self.state._field_accesses[(obj, field)]
            self.subscribe(Ctx(obj.context, None, access))
        for registers in tuple(self.state._heap.heap.values()):
            for variable in tuple(registers.values()):
                self.variable_created(variable)
        for captures in (
            self.state._heap.cell_vars,
            self.state._heap.nonlocal_vars,
            self.state._heap.global_vars,
        ):
            for variables in tuple(captures.values()):
                self.captures_created(variables)
        self.events.extend(
            (
                {"kind": "heap_havoc", "origin": origin, "scope": "all_known_and_future_fields"},
                {"kind": "unknown_callback", "origin": origin, "target": "*", "order": "unknown"},
                {
                    "kind": "unknown_exception",
                    "origin": origin,
                    "exception": "BaseException",
                    "order": "unknown",
                },
            )
        )

    def subscribe(self, variable: Ctx[Variable | FieldAccess]) -> None:
        if self.source is None or variable == self.source or variable in self.subscriptions:
            return
        self.subscriptions.add(variable)
        self.state._add_var_points_flow(self.source, variable)

    def field_created(self, obj: AbstractObject, field_access: FieldAccess) -> None:
        self.subscribe(Ctx(obj.context, None, field_access))

    def variable_created(self, variable: Ctx[Variable]) -> None:
        value = variable.content
        if value.kind in {
            VariableKind.GLOBAL,
            VariableKind.CELL,
            VariableKind.NONLOCAL,
        } and not value.name.startswith(("$", "<")):
            self.subscribe(variable)

    def captures_created(self, variables: Mapping[str, Ctx[Variable]]) -> None:
        for variable in tuple(variables.values()):
            # A captured cell can be implemented by a PARAMETER/LOCAL slot.
            self.subscribe(variable)

    def snapshot(self) -> tuple[JsonObject, ...]:
        return tuple(dict(event) for event in self.events)
