"""Private compact representation for Closure's ordinary liveness equations.

Public dataflow liveness keeps its mutable set facts. This solver is selected
only by Closure's non-retaining consumer. Its diagnostic mappings decode sets
on demand; the hot path stores integer masks and decodes only the entry fact.
"""

from collections.abc import Iterable, Iterator, Mapping
from typing import Literal, TypedDict, override

from pythonstan.graph.cfg import BaseBlock, CFGEdge, ControlFlowGraph
from pythonstan.ir import IRScope

from .dataflow.execution import solve_with_custom_solver
from .dataflow.liveness import LivenessAnalysis
from .dataflow.solver import WorklistSolver

# An injected/custom analysis is not licensed by the ordinary kill/gen proof.
_ORDINARY_TRANSFER: object = LivenessAnalysis.transfer_node
_ORDINARY_EDGE_QUERY: object = LivenessAnalysis.need_transfer_edge
_ORDINARY_MEET: object = LivenessAnalysis.meet
_ORDINARY_INITIAL: object = LivenessAnalysis.new_init_fact
_ORDINARY_BOUNDARY: object = LivenessAnalysis.new_boundary_fact
_ORDINARY_CFG_QUERY: object = LivenessAnalysis.get_cfg


class LivenessStatistics(TypedDict):
    representation: Literal["interned_integer_masks"]
    nodes: int
    names: int
    transfer_calls: int
    encoded_fact_slots: int
    decoded_fact_cache_entries: int


class DecodedLivenessFacts(Mapping[BaseBlock, set[str]]):
    """Read-only node map; each lookup returns a fresh ordinary mutable set.

    Observing or mutating a decoded copy cannot alter the encoded fixed point.
    No decoded-node cache is retained: a diagnostic that walks every node owns
    its own observation cost. Generic public liveness still returns dicts of
    persistent mutable sets, including explicitly retained scope results.
    """

    def __init__(
        self,
        facts: Mapping[BaseBlock, int],
        names: tuple[str, ...],
        statistics: LivenessStatistics,
    ) -> None:
        self._facts: Mapping[BaseBlock, int] = facts
        self._names: tuple[str, ...] = names
        self.statistics: LivenessStatistics = statistics

    @override
    def __getitem__(self, node: BaseBlock) -> set[str]:
        mask = self._facts[node]
        result: set[str] = set()
        while mask:
            low = mask & -mask
            result.add(self._names[low.bit_length() - 1])
            mask ^= low
        return result

    @override
    def __iter__(self) -> Iterator[BaseBlock]:
        return iter(self._facts)

    @override
    def __len__(self) -> int:
        return len(self._facts)


class _MaskLiveness:
    """The same finite powerset domain transported through an injective codec."""

    is_forward: bool = False

    def __init__(self, analysis: LivenessAnalysis) -> None:
        self.cfg: ControlFlowGraph = analysis.cfg
        self.names: list[str] = []
        self.name_indices: dict[str, int] = {}
        self.transfers: dict[BaseBlock, tuple[int, int]] = {}
        self.transfer_calls: int = 0
        # Child cell/nonlocal facts are already materialized by Closure's
        # child-first traversal. Compiler-generated names use the same equality
        # and hashing as the original set domain; no textual normalization.
        for node in self.cfg.blks:
            kill = generate = 0
            for statement in reversed(node.stmts):
                stores = self.encode(statement.get_stores())
                uses = self.encode(statement.get_nostores())
                if isinstance(statement, IRScope):
                    uses |= self.encode(statement.get_cell_vars() | statement.get_nonlocal_vars())
                kill |= stores
                generate = (generate & ~stores) | uses
            self.transfers[node] = (kill, generate)

    def encode(self, names: Iterable[str]) -> int:
        mask = 0
        for name in names:
            index = self.name_indices.get(name)
            if index is None:
                index = len(self.names)
                self.name_indices[name] = index
                self.names.append(name)
            mask |= 1 << index
        return mask

    def get_cfg(self) -> ControlFlowGraph:
        return self.cfg

    def new_init_fact(self) -> int:
        return 0

    def new_boundary_fact(self) -> int:
        return 0

    def need_transfer_edge(self, edge: CFGEdge) -> bool:
        return False

    def transfer_edge(self, edge: CFGEdge, fact: int) -> int:
        return fact

    def meet(self, first: int, second: int) -> int:
        return first | second

    def transfer_node(self, node: BaseBlock, fact: int) -> int:
        self.transfer_calls += 1
        kill, generate = self.transfers[node]
        return (fact & ~kill) | generate


def _same_method(analysis: LivenessAnalysis, name: str, expected: object) -> bool:
    method: object = getattr(analysis, name)
    implementation: object = getattr(method, "__func__", None)
    return implementation is expected


class CompactLivenessSolver:
    """Reuse the existing worklist with exact integer-domain correspondence.

    Arbitrary customized analyses retain their ordinary solver maps. This
    compatibility entry point does not invent their erased generic Fact type.
    Closure validates its actual variable-name facts at its own consumer.
    """

    @classmethod
    def solve(
        cls, analysis: object
    ) -> tuple[Mapping[BaseBlock, object], Mapping[BaseBlock, object]]:
        ordinary_methods = (
            ("transfer_node", _ORDINARY_TRANSFER),
            ("need_transfer_edge", _ORDINARY_EDGE_QUERY),
            ("meet", _ORDINARY_MEET),
            ("new_init_fact", _ORDINARY_INITIAL),
            ("new_boundary_fact", _ORDINARY_BOUNDARY),
            ("get_cfg", _ORDINARY_CFG_QUERY),
        )
        if not (
            type(analysis) is LivenessAnalysis
            and isinstance(analysis, LivenessAnalysis)
            and analysis.is_forward is False
            and all(_same_method(analysis, name, method) for name, method in ordinary_methods)
        ):
            facts = solve_with_custom_solver(WorklistSolver, analysis)
            return facts.incoming, facts.outgoing
        encoded = _MaskLiveness(analysis)
        facts_in, facts_out = WorklistSolver[int].solve(encoded)
        names = tuple(encoded.names)
        statistics: LivenessStatistics = {
            "representation": "interned_integer_masks",
            "nodes": len(facts_in),
            "names": len(names),
            "transfer_calls": encoded.transfer_calls,
            "encoded_fact_slots": len(facts_in) + len(facts_out),
            "decoded_fact_cache_entries": 0,
        }
        return (
            DecodedLivenessFacts(facts_in, names, statistics),
            DecodedLivenessFacts(facts_out, names, statistics),
        )
