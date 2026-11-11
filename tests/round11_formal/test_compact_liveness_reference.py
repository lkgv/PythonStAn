"""Independent set equations for the private liveness representation change.

The oracle recomputes every node using ordinary sets until no equation changes.
It uses neither the generic worklist nor compact transfer composition. The
statement accessors are the trusted semantic interface shared by both domains.
"""
import ast
import random

import pytest

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.compact_liveness import CompactLivenessSolver, DecodedLivenessFacts
from pythonstan.analysis.dataflow.liveness import LivenessAnalysis
from pythonstan.analysis.dataflow.solver import WorklistSolver
from pythonstan.graph.cfg import BaseBlock, ControlFlowGraph
from pythonstan.graph.cfg.edges import NormalEdge
from pythonstan.ir import IRFunc, IRModule, IRScope


class Symbols:
    """A pure IR-statement interface fixture; order inside a block matters."""
    def __init__(self, stores=(), uses=()):
        self.stores, self.uses = frozenset(stores), frozenset(uses)
    def get_stores(self): return set(self.stores)
    def get_nostores(self): return set(self.uses)


def graph(seed):
    randomizer = random.Random(seed)
    names = ["captured", "__class__", "args", "kwargs", "$private@1", "temp1", "temp2", "λ"]
    blocks = []
    for index in range(9):
        statements = [Symbols(randomizer.sample(names, randomizer.randrange(4)),
                              randomizer.sample(names, randomizer.randrange(4)))
                      for _ in range(randomizer.randrange(1, 4))]
        blocks.append(BaseBlock(index, statements))
    # Composition adversary: an early store kills a later use, whereas an
    # earlier use survives the later store under backward statement order.
    blocks[2].stmts = [Symbols(["temp1"], ["temp2"]), Symbols(["temp2"], ["temp1"])]
    nested = IRFunc("same.same", ast.parse("def same(arg):\n return arg\n").body[0])
    nested.cell_vars = {"captured", "__class__"}
    nested.nonlocal_vars = {"kwargs", "$private@1"}
    blocks[3].stmts.append(nested)
    # This intentionally disconnected block still has ordinary liveness facts.
    blocks[8].stmts = [Symbols((), ["disconnected", "temp1", "temp2", "λ"])]
    exit_block = BaseBlock(9)
    cfg = ControlFlowGraph(blocks[0])
    for block in [*blocks, exit_block]: cfg.add_blk(block)
    cfg.super_exit_blk = exit_block
    edges = {(0, 1), (1, 2), (2, 3), (3, 1), (3, 4), (3, 5),
             (4, 6), (5, 6), (6, 6), (6, 7), (7, 9)}
    for _ in range(5): edges.add((randomizer.randrange(8), randomizer.randrange(8)))
    order = list(sorted(edges)); randomizer.shuffle(order)
    by_index = {block.idx: block for block in [*blocks, exit_block]}
    for source, target in order: cfg.add_edge(NormalEdge(by_index[source], by_index[target]))
    module = IRModule("independent_liveness", ast.parse(""), filename="independent_liveness.py")
    analysis = LivenessAnalysis(module, cfg, AnalysisConfig("live", "LivenessAnalysis",
                                                           options={"type": "dataflow analysis"}))
    return analysis


def set_equations(analysis):
    cfg = analysis.cfg
    before = {node: set() for node in cfg.blks}
    after = {node: set() for node in cfg.blks}
    # A deliberately dense sweep, not the production predecessor worklist.
    while True:
        changed = False
        for node in sorted(cfg.blks, key=lambda block: block.idx):
            if node is cfg.super_exit_blk: continue
            out = set().union(*(before[edge.tgt] for edge in cfg.out_edges_of(node)))
            incoming = set(out)
            for statement in reversed(node.stmts):
                incoming -= statement.get_stores()
                incoming |= statement.get_nostores()
                if isinstance(statement, IRScope):
                    incoming |= statement.get_cell_vars()
                    incoming |= statement.get_nonlocal_vars()
            if before[node] != incoming or after[node] != out:
                changed = True
                before[node], after[node] = incoming, out
        if not changed: return before, after


@pytest.mark.parametrize("seed", range(16))
def test_every_decoded_fact_equals_independent_fixed_point_on_cycles_and_disconnected_blocks(seed):
    analysis = graph(seed)
    expected_in, expected_out = set_equations(analysis)
    actual_in, actual_out = CompactLivenessSolver.solve(analysis)
    assert isinstance(actual_in, DecodedLivenessFacts)
    assert isinstance(actual_out, DecodedLivenessFacts)
    assert dict(actual_in) == expected_in
    assert dict(actual_out) == expected_out
    assert actual_in[analysis.cfg.super_exit_blk] == set()
    disconnected = next(node for node in analysis.cfg.blks if node.idx == 8)
    assert actual_in[disconnected] == {"disconnected", "temp1", "temp2", "λ"}
    assert {"temp1", "temp2", "λ"} <= set().union(*actual_in.values(), *actual_out.values())


def test_decoded_mutations_cannot_alias_encoded_solution_or_other_node_observations():
    analysis = graph(81)
    facts_in, facts_out = CompactLivenessSolver.solve(analysis)
    node = analysis.cfg.get_entry()
    expected = set(facts_in[node])
    observation = facts_in[node]
    observation.clear(); observation.add("external mutation")
    assert facts_in[node] == expected
    assert facts_in[node] is not facts_in[node]
    with pytest.raises(TypeError): facts_in[node] = {"replace node"}
    assert all("external mutation" not in values for values in facts_out.values())


def test_customized_direction_falls_back_to_ordinary_analysis():
    analysis = graph(17)
    analysis.is_forward = True
    expected = WorklistSolver.solve(analysis)
    actual = CompactLivenessSolver.solve(analysis)
    assert all(type(mapping) is dict for mapping in actual)
    assert actual == expected


def test_customized_cfg_accessor_is_not_silently_replaced_by_compact_helper():
    analysis = graph(18)
    analysis.get_cfg = lambda: analysis.cfg
    expected = WorklistSolver.solve(analysis)
    actual = CompactLivenessSolver.solve(analysis)
    assert all(type(mapping) is dict for mapping in actual)
    assert actual == expected


def test_customized_edge_semantics_fall_back_with_boundary_value_preserved():
    analysis = graph(9)
    analysis.need_transfer_edge = lambda edge: True
    analysis.transfer_edge = lambda edge, fact: fact | {"edge-specific"}
    analysis.new_boundary_fact = lambda: {"custom exit"}
    expected = WorklistSolver.solve(analysis)
    actual = CompactLivenessSolver.solve(analysis)
    assert all(type(mapping) is dict for mapping in actual)
    assert actual == expected
    assert "custom exit" in actual[0][analysis.cfg.super_exit_blk]
