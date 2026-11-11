"""Compare complete finite-lattice solutions, including unscheduled reachability."""
from collections import Counter
import random

import pytest

from pythonstan.analysis.dataflow.solver import Solver, WorklistSolver
from pythonstan.graph.cfg import BaseBlock, ControlFlowGraph, NormalEdge


class GenKill:
    def __init__(self, cfg, *, forward, transfer_edges=False):
        self.cfg = cfg
        self.is_forward = forward
        self.transfer_edges = transfer_edges
        self.calls = Counter()

    def get_cfg(self): return self.cfg
    def new_init_fact(self): return set()
    def new_boundary_fact(self): return {"boundary"}
    def need_transfer_edge(self, edge): return self.transfer_edges
    def transfer_edge(self, edge, fact): return fact | {f"edge:{edge.src.idx}:{edge.tgt.idx}"}
    def meet(self, first, second): return first | second
    def transfer_node(self, node, fact):
        self.calls[node] += 1
        return (fact - {f"node:{node.idx - 1}"}) | {f"node:{node.idx}"}


def reference_round_robin(analysis):
    """Independent simultaneous rounds over every node of this finite powerset."""
    cfg = analysis.cfg
    incoming = {n: set() for n in cfg.blks}
    outgoing = {n: set() for n in cfg.blks}
    boundary = cfg.entry_blk if analysis.is_forward else cfg.super_exit_blk
    incoming[boundary] = outgoing[boundary] = {"boundary"}
    for _ in range(1000):
        next_in = {n: set(f) for n, f in incoming.items()}
        next_out = {n: set(f) for n, f in outgoing.items()}
        for node in cfg.blks - {boundary}:
            fact = set()
            edges = cfg.in_edges_of(node) if analysis.is_forward else cfg.out_edges_of(node)
            for edge in edges:
                addition = outgoing[edge.src] if analysis.is_forward else incoming[edge.tgt]
                if analysis.need_transfer_edge(edge): addition = analysis.transfer_edge(edge, addition)
                fact |= addition
            changed = analysis.transfer_node(node, fact)
            if analysis.is_forward:
                next_in[node], next_out[node] = fact, changed
            else:
                next_in[node], next_out[node] = changed, fact
        if next_in == incoming and next_out == outgoing:
            return incoming, outgoing
        incoming, outgoing = next_in, next_out
    raise AssertionError("Finite reference domain did not converge")


def make_graph(seed):
    nodes = [BaseBlock(i, []) for i in range(10)]
    cfg = ControlFlowGraph(nodes[0])
    for node in nodes: cfg.add_blk(node)
    cfg.super_exit_blk = nodes[-1]
    # Entry and super-exit have no incoming/outgoing edges respectively.
    pairs = {(0, 1), (1, 2), (2, 3), (3, 9), (1, 4), (4, 3), (2, 1),
             (6, 6), (7, 8)}  # Completely disconnected self-loop and component.
    rng = random.Random(seed)
    for _ in range(12):
        pairs.add((rng.randrange(1, 5), rng.randrange(1, 5)))
    for source, target in sorted(pairs): cfg.add_edge(NormalEdge(nodes[source], nodes[target]))
    return cfg


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("forward", [False, True])
@pytest.mark.parametrize("transfer_edges", [False, True])
def test_complete_facts_equal_independent_reference(seed, forward, transfer_edges):
    cfg = make_graph(seed)
    actual = GenKill(cfg, forward=forward, transfer_edges=transfer_edges)
    expected = GenKill(cfg, forward=forward, transfer_edges=transfer_edges)
    result = WorklistSolver.solve(actual)
    assert result == reference_round_robin(expected)
    assert set(result[0]) == cfg.blks == set(result[1])
    disconnected_loop = next(n for n in cfg.blks if n.idx == 6)
    assert actual.calls[disconnected_loop] >= 2
    assert "node:6" in result[0][disconnected_loop] & result[1][disconnected_loop]


@pytest.mark.parametrize("forward", [False, True])
def test_edge_transfer_degree_one_has_initial_fact(forward):
    nodes = [BaseBlock(i, []) for i in range(3)]
    cfg = ControlFlowGraph(nodes[0])
    for node in nodes: cfg.add_blk(node)
    cfg.super_exit_blk = nodes[-1]
    cfg.add_edge(NormalEdge(nodes[0], nodes[1])); cfg.add_edge(NormalEdge(nodes[1], nodes[2]))
    analysis = GenKill(cfg, forward=forward, transfer_edges=True)
    facts = WorklistSolver.solve(analysis)
    assert facts == reference_round_robin(GenKill(cfg, forward=forward, transfer_edges=True))
