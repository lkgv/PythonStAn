from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import override

from pythonstan.analysis.contracts.json_data import JsonObject

from .context import Ctx
from .debug_boundary import feature, record_pfg_edge_activated
from .object import ClassObject, InstanceObject
from .points_to_set import PointsToSet
from .variable import FieldAccess, Variable

__all__ = [
    "PointerFlowGraph",
    "PointerFlowEdge",
    "PointerFlowNode",
    "NormalNode",
    "GuardNode",
    "SelectorNode",
    "InheritanceSelectorNode",
    "PointerFlowKind",
    "propagation_pair",
]


def propagation_pair(
    row: "list[PointerFlowNode | PointsToSet]",
) -> "tuple[PointerFlowNode, PointsToSet]":
    """Decode the real mutable propagation row without changing its public shape."""
    if len(row) != 2:
        raise ValueError("Propagation row must contain exactly two items")
    node, points = row
    if not isinstance(node, PointerFlowNode) or not isinstance(points, PointsToSet):
        raise TypeError("Propagation row must contain a node followed by its points-to set")
    return node, points


def _edge_count(item: "tuple[PointerFlowEdge, int]") -> int:
    return item[1]


class PointerFlowKind(Enum):
    """Kinds of points-to flow in pointer flow graph."""

    NORMAL = "normal"
    INHERIT = "inherit"
    INSTANCE = "instance"


@dataclass(frozen=True)
class PointerFlowEdge:
    source: "PointerFlowNode"
    target: "PointerFlowNode"
    kind: PointerFlowKind

    def __post_init__(self) -> None:
        assert isinstance(self.source, PointerFlowNode)
        assert isinstance(self.target, PointerFlowNode)
        assert self.source != self.target, (
            f"source and target cannot be the same: {self.source} -> {self.target}"
        )
        if self.kind == PointerFlowKind.INHERIT:
            self._class_receiver()
        if self.kind == PointerFlowKind.INSTANCE:
            self._instance_receiver()

    def _class_receiver(self) -> ClassObject:
        assert isinstance(self.target, NormalNode)
        access = self.target.var.content
        assert isinstance(access, FieldAccess)
        assert isinstance(access.obj, ClassObject)
        return access.obj

    def _instance_receiver(self) -> InstanceObject:
        assert isinstance(self.target, NormalNode)
        access = self.target.var.content
        assert isinstance(access, FieldAccess)
        assert isinstance(access.obj, InstanceObject)
        return access.obj

    def flow_through(self, pts: "PointsToSet") -> "PointsToSet":
        if self.kind == PointerFlowKind.INHERIT:
            return pts.inherit_to(self._class_receiver())
        if self.kind == PointerFlowKind.INSTANCE:
            return pts.deliver_into(self._instance_receiver())
        return pts


class PointerFlowNode(ABC):
    @abstractmethod
    def flow_through(self, edge: PointerFlowEdge, pts: "PointsToSet") -> "PointsToSet":
        pass


@dataclass(frozen=True)
class NormalNode(PointerFlowNode):
    """
    Normal node in pointer flow graph, just make all objects flowed through.
    var: A variable or field access with its actual Native context.
    """

    var: Ctx[Variable | FieldAccess]

    def __post_init__(self) -> None:
        assert isinstance(self.var, Ctx), f"var must be a Ctx, but got {type(self.var)}"

    @override
    def flow_through(self, edge: PointerFlowEdge, pts: "PointsToSet") -> "PointsToSet":
        return pts


class GuardNode(PointerFlowNode):
    """
    Guardian node in pointer flow graph, only allow the objects that are allowed by the guard condition to be flowed through.
    guard: Callable[[PointerFlowEdge, PointsToSet], PointsToSet] - the guard condition.
    """

    def __init__(self, guard: "Callable[[PointerFlowEdge, PointsToSet], PointsToSet]") -> None:
        self.guard = guard

    @override
    def __hash__(self) -> int:
        return id(self)

    @override
    def __eq__(self, other: object) -> bool:
        return self is other

    @override
    def flow_through(self, edge: PointerFlowEdge, pts: "PointsToSet") -> "PointsToSet":
        return self.guard(edge, pts)


class SelectorNode(PointerFlowNode):
    """Selector node in pointer flow graph, only the edge with the least index will be flowed through.
    edges: Dict[PointerFlowEdge, int] - the edges and their indices.
    least_index: int - the least index of the edges.
    """

    edges: dict[PointerFlowEdge, int]
    least_index: int

    def __init__(self, least_index: int = -1) -> None:
        self.edges = {}
        self.least_index = least_index

    @override
    def __hash__(self) -> int:
        return id(self)

    @override
    def __eq__(self, other: object) -> bool:
        return self is other

    def add_edge(self, edge: PointerFlowEdge, index: int) -> None:
        self.edges[edge] = index

    @override
    def flow_through(self, edge: PointerFlowEdge, pts: "PointsToSet") -> "PointsToSet":
        """Allow flow from edges with minimum index (MRO semantics).

        For multiple inheritance: when the same field appears in multiple parents,
        only the first parent (smallest index) should provide the value.
        Empty sets don't affect MRO ordering - we only consider edges that
        actually provide objects.
        """
        assert edge in self.edges, f"edge {edge} not found in selector node"

        # If points-to set is empty, don't update state - just pass through
        # This ensures empty fields from higher-priority parents don't block
        # non-empty fields from lower-priority parents
        if pts.is_empty():
            return pts

        index = self.edges[edge]

        # Allow flow if this is the first non-empty edge OR if index matches/beats current minimum
        if self.least_index == -1 or index <= self.least_index:
            # Update least_index only if this index is actually smaller
            if self.least_index == -1 or index < self.least_index:
                self.least_index = index
            return pts
        # Block flow from higher-index edges (lower priority in MRO)
        return PointsToSet.empty()


class InheritanceSelectorNode(SelectorNode):
    """Join inherited may-values without inferring must-presence.

    One abstract class/base may represent executions where a field is absent.
    A nonempty may set on an earlier base therefore cannot suppress a later
    base. Keep the declared order metadata, but require a separate must domain
    before using it to remove candidates.
    """

    @override
    def flow_through(self, edge: PointerFlowEdge, pts: "PointsToSet") -> "PointsToSet":
        assert edge in self.edges
        return pts


class PointerFlowGraph:
    """Represents pointer flow graph in context-sensitive pointer analysis."""

    # TODO maybe object can be grouped by age, and too old objects should not be propagated.

    succs: dict[PointerFlowNode, set[PointerFlowEdge]]
    preds: dict[PointerFlowNode, set[PointerFlowEdge]]
    nodes: set[PointerFlowNode]
    edges: set[PointerFlowEdge]

    def __init__(self, debug_monitor: object | None = None) -> None:
        """Initialize pointer flow graph.

        Args:
            debug_monitor: Optional DebugMonitor instance for tracking
        """
        self.succs = {}
        self.preds = {}
        self.nodes = set()
        self.edges = set()

        # Debug monitoring
        self._debug_monitor = debug_monitor
        self._edge_activation_counts: dict[PointerFlowEdge, int] = {}
        self._edge_object_flow: dict[PointerFlowEdge, int] = {}

    def propagate(
        self, node: PointerFlowNode, pts: "PointsToSet"
    ) -> "list[list[PointerFlowNode | PointsToSet]]":
        assert isinstance(node, PointerFlowNode), f"node must be a PFNode, but got {type(node)}"
        result: list[list[PointerFlowNode | PointsToSet]] = []
        for succ_edge in self.succs.get(node, frozenset()):
            succ_pts = succ_edge.flow_through(pts)
            if succ_pts.is_empty():
                continue
            succ_pts = succ_edge.target.flow_through(succ_edge, succ_pts)
            if succ_pts.is_empty():
                continue

            # Track edge activation
            num_objects = len(succ_pts)
            self._edge_activation_counts[succ_edge] = (
                self._edge_activation_counts.get(succ_edge, 0) + 1
            )
            self._edge_object_flow[succ_edge] = (
                self._edge_object_flow.get(succ_edge, 0) + num_objects
            )

            # Debug monitoring
            if (
                self._debug_monitor
                and feature(self._debug_monitor, "enabled")
                and feature(self._debug_monitor, "track_pfg")
            ):
                edge_id = f"{id(succ_edge.source)}->{id(succ_edge.target)}"
                record_pfg_edge_activated(self._debug_monitor, edge_id, num_objects)

            result.append([succ_edge.target, succ_pts])
        return result

    def add_edge(self, edge: PointerFlowEdge) -> bool:
        if edge not in self.edges:
            self.succs.setdefault(edge.source, {*()}).add(edge)
            self.preds.setdefault(edge.target, {*()}).add(edge)
            self.nodes.add(edge.source)
            self.nodes.add(edge.target)
            self.edges.add(edge)
            return True
        return False

    def flow_through_edge(self, edge: PointerFlowEdge, pts: "PointsToSet") -> "PointsToSet":
        return edge.target.flow_through(edge, edge.flow_through(pts))

    def get_succs(self, var: PointerFlowNode) -> set[PointerFlowEdge]:
        return self.succs.get(var, {*()})

    def get_preds(self, var: PointerFlowNode) -> set[PointerFlowEdge]:
        return self.preds.get(var, {*()})

    def get_nodes(self) -> set[PointerFlowNode]:
        return self.nodes

    def get_edges(self) -> set[PointerFlowEdge]:
        return self.edges

    def get_edge_statistics(self) -> JsonObject:
        """Get PFG edge activation statistics.

        Returns:
            Dictionary with edge statistics:
            - total_edges: Total number of edges
            - activated_edges: Number of edges that activated at least once
            - dead_edges: Number of edges that never activated
            - total_activations: Sum of all activation counts
            - total_object_flow: Total objects flowed through all edges
            - most_active_edges: Top edges by activation count
            - least_active_edges: Edges with low activation
        """
        activated = [e for e, count in self._edge_activation_counts.items() if count > 0]
        dead = [e for e in self.edges if e not in self._edge_activation_counts]

        # Sort by activation count
        sorted_by_count = sorted(
            self._edge_activation_counts.items(), key=_edge_count, reverse=True
        )

        # Sort by object flow
        sorted_by_flow = sorted(self._edge_object_flow.items(), key=_edge_count, reverse=True)

        return {
            "total_edges": len(self.edges),
            "activated_edges": len(activated),
            "dead_edges": len(dead),
            "activation_rate": len(activated) / len(self.edges) if self.edges else 0.0,
            "total_activations": sum(self._edge_activation_counts.values()),
            "total_object_flow": sum(self._edge_object_flow.values()),
            "avg_activations_per_edge": (
                sum(self._edge_activation_counts.values()) / len(activated) if activated else 0.0
            ),
            "avg_object_flow_per_edge": (
                sum(self._edge_object_flow.values()) / len(activated) if activated else 0.0
            ),
            "most_active_edges": [
                {"edge_id": f"{id(e.source)}->{id(e.target)}", "kind": e.kind.value, "count": count}
                for e, count in sorted_by_count[:10]
            ],
            "highest_flow_edges": [
                {
                    "edge_id": f"{id(e.source)}->{id(e.target)}",
                    "kind": e.kind.value,
                    "objects": flow,
                }
                for e, flow in sorted_by_flow[:10]
            ],
        }
