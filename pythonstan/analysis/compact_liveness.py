"""Private compact representation for Closure's ordinary liveness equations.

Public dataflow liveness keeps its mutable set facts. This solver is selected
only by Closure's non-retaining consumer. Its diagnostic mappings decode sets
on demand; the hot path stores integer masks and decodes only the entry fact.
"""
from collections.abc import Mapping

from pythonstan.ir import IRScope
from .dataflow.liveness import LivenessAnalysis
from .dataflow.solver import WorklistSolver


# An injected/custom analysis is not licensed by the ordinary kill/gen proof.
_ORDINARY_TRANSFER = LivenessAnalysis.transfer_node
_ORDINARY_EDGE_QUERY = LivenessAnalysis.need_transfer_edge
_ORDINARY_MEET = LivenessAnalysis.meet
_ORDINARY_INITIAL = LivenessAnalysis.new_init_fact
_ORDINARY_BOUNDARY = LivenessAnalysis.new_boundary_fact
_ORDINARY_CFG_QUERY = LivenessAnalysis.get_cfg


class DecodedLivenessFacts(Mapping):
    """Read-only node map; each lookup returns a fresh ordinary mutable set.

    Observing or mutating a decoded copy cannot alter the encoded fixed point.
    No decoded-node cache is retained: a diagnostic that walks every node owns
    its own observation cost. Generic public liveness still returns dicts of
    persistent mutable sets, including explicitly retained scope results.
    """

    def __init__(self, facts, names, statistics):
        self._facts = facts
        self._names = names
        self.statistics = statistics

    def __getitem__(self, node):
        mask = self._facts[node]
        result = set()
        while mask:
            low = mask & -mask
            result.add(self._names[low.bit_length() - 1])
            mask ^= low
        return result

    def __iter__(self):
        return iter(self._facts)

    def __len__(self):
        return len(self._facts)


class _MaskLiveness:
    """The same finite powerset domain transported through an injective codec."""

    is_forward = False

    def __init__(self, analysis):
        self.cfg = analysis.cfg
        self.names = []
        self.name_indices = {}
        self.transfers = {}
        self.transfer_calls = 0
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

    def encode(self, names):
        mask = 0
        for name in names:
            index = self.name_indices.get(name)
            if index is None:
                index = len(self.names)
                self.name_indices[name] = index
                self.names.append(name)
            mask |= 1 << index
        return mask

    def get_cfg(self):
        return self.cfg

    def new_init_fact(self):
        return 0

    def new_boundary_fact(self):
        return 0

    def need_transfer_edge(self, edge):
        return False

    def meet(self, first, second):
        return first | second

    def transfer_node(self, node, fact):
        self.transfer_calls += 1
        kill, generate = self.transfers[node]
        return (fact & ~kill) | generate


class CompactLivenessSolver:
    """Reuse the existing worklist with exact integer-domain correspondence."""

    @classmethod
    def solve(cls, analysis):
        ordinary_methods = (
            ("transfer_node", _ORDINARY_TRANSFER),
            ("need_transfer_edge", _ORDINARY_EDGE_QUERY),
            ("meet", _ORDINARY_MEET),
            ("new_init_fact", _ORDINARY_INITIAL),
            ("new_boundary_fact", _ORDINARY_BOUNDARY),
            ("get_cfg", _ORDINARY_CFG_QUERY),
        )
        if (type(analysis) is not LivenessAnalysis or analysis.is_forward is not False or any(
                getattr(getattr(analysis, name), "__func__", None) is not method
                for name, method in ordinary_methods)):
            return WorklistSolver.solve(analysis)
        encoded = _MaskLiveness(analysis)
        facts_in, facts_out = WorklistSolver.solve(encoded)
        names = tuple(encoded.names)
        statistics = {"representation": "interned_integer_masks", "nodes": len(facts_in),
                      "names": len(names), "transfer_calls": encoded.transfer_calls,
                      "encoded_fact_slots": len(facts_in) + len(facts_out),
                      "decoded_fact_cache_entries": 0}
        return (DecodedLivenessFacts(facts_in, names, statistics),
                DecodedLivenessFacts(facts_out, names, statistics))
