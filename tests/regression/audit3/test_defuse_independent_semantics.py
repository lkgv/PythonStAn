"""Independent finite def-use witnesses through actual IR, CFG and analysis APIs."""

from __future__ import annotations

import ast

import pytest

from pythonstan.analysis.analysis import AnalysisConfig
from pythonstan.analysis.dataflow.reaching_definition import ReachingDefinitionAnalysis
from pythonstan.analysis.defuse import DefUseAnalysis
from pythonstan.graph.cfg import BaseBlock, CFGEdge, ControlFlowGraph
from pythonstan.ir import IRAstStmt, IRCopy, IRModule, IRStatement
from pythonstan.utils.var_collector import VarCollector
from pythonstan.world.world import World


class _Edge(CFGEdge):
    def __init__(self, source: BaseBlock, target: BaseBlock) -> None:
        self.src = source
        self.tgt = target


class _Graph(ControlFlowGraph):
    """Initialize the real CFG relation without relying on its untyped constructor."""

    def __init__(self, blocks: tuple[BaseBlock, ...], edges: tuple[tuple[int, int], ...]) -> None:
        self.entry_blk = blocks[0]
        self.super_exit_blk = blocks[-1]
        self.exit_blks = {blocks[-1]}
        self.blks = set(blocks)
        self.stmts = {statement for block in blocks for statement in block.stmts}
        self.in_edges = {block: [] for block in blocks}
        self.out_edges = {block: [] for block in blocks}
        self.label2blk = {}
        self.blk2label = {}
        self.var_collector = VarCollector()
        for source, target in edges:
            edge = _Edge(blocks[source], blocks[target])
            self.in_edges[blocks[target]].append(edge)
            self.out_edges[blocks[source]].append(edge)


def _copy(targets: tuple[str, ...], source: str) -> IRStatement:
    assignment = ast.Assign(
        targets=[ast.Name(id=target, ctx=ast.Store()) for target in targets],
        value=ast.Name(id=source, ctx=ast.Load()),
    )
    # IRCopy's normalized contract has exactly one destination. The generic
    # AST-backed IR records every store of a compound defining statement.
    return IRCopy(assignment) if len(targets) == 1 else IRAstStmt(assignment)


def _install(graph: ControlFlowGraph) -> IRModule:
    World.setup()
    World().scope_manager.build()
    scope = IRModule("defuse_review", ast.parse(""), filename="defuse_review.py")
    World().scope_manager.set_ir(scope, "cfg", graph)
    World().scope_manager.set_ir(
        scope,
        "ir",
        [
            statement
            for block in sorted(graph.blks, key=lambda item: item.idx)
            for statement in block.stmts
        ],
    )
    return scope


def _driver(*, stores: bool = True, loads: bool = True) -> DefUseAnalysis:
    return DefUseAnalysis(
        AnalysisConfig(
            "defuse_review",
            "DefUseAnalysis",
            options={"type": "dataflow analysis", "compute-stores": stores, "compute-loads": loads},
        )
    )


def test_defuse_reads_statement_prefix_and_publishes_real_result() -> None:
    first = _copy(("x",), "seed")
    before = _copy(("before",), "x")
    replacement = _copy(("x",), "other")
    after = _copy(("after",), "x")
    block = BaseBlock(1, [first, before, replacement, after])
    graph = _Graph((BaseBlock(0), block, BaseBlock(2)), ((0, 1), (1, 2)))
    scope = _install(graph)
    driver = _driver()

    stores, loads = driver.analyze(scope)

    assert stores[(before, "x")] == {first}
    assert stores[(after, "x")] == {replacement}
    assert loads[first] == {before}
    assert loads[replacement] == {after}
    assert driver.results == (stores, loads)


def test_defuse_joins_branch_definitions_without_using_overwritten_entry() -> None:
    initial = _copy(("x",), "seed")
    left, right = _copy(("x",), "left"), _copy(("x",), "right")
    use = _copy(("result",), "x")
    blocks = (
        BaseBlock(0),
        BaseBlock(1, [initial]),
        BaseBlock(2, [left]),
        BaseBlock(3, [right]),
        BaseBlock(4, [use]),
        BaseBlock(5),
    )
    scope = _install(_Graph(blocks, ((0, 1), (1, 2), (1, 3), (2, 4), (3, 4), (4, 5))))

    stores, _ = _driver().analyze(scope)

    assert stores[(use, "x")] == {left, right}


def test_defuse_keeps_initial_and_backedge_reaching_definitions() -> None:
    initial = _copy(("x",), "seed")
    loop_step = _copy(("x",), "x")
    use = _copy(("result",), "x")
    blocks = (
        BaseBlock(0),
        BaseBlock(1, [initial]),
        BaseBlock(2, [loop_step]),
        BaseBlock(3, [use]),
        BaseBlock(4),
    )
    graph = _Graph(blocks, ((0, 1), (1, 2), (1, 3), (2, 2), (2, 3), (3, 4)))
    scope = _install(graph)

    stores, _ = _driver().analyze(scope)

    assert stores[(use, "x")] == {initial, loop_step}
    assert stores[(loop_step, "x")] == {initial, loop_step}


@pytest.mark.parametrize("stores_enabled,loads_enabled", [(True, False), (False, True)])
def test_defuse_options_control_only_the_requested_relation(
    stores_enabled: bool, loads_enabled: bool
) -> None:
    definition, use = _copy(("x",), "seed"), _copy(("result",), "x")
    graph = _Graph((BaseBlock(0), BaseBlock(1, [definition, use]), BaseBlock(2)), ((0, 1), (1, 2)))
    scope = _install(graph)

    stores, loads = _driver(stores=stores_enabled, loads=loads_enabled).analyze(scope)

    assert bool(stores) is stores_enabled
    assert bool(loads) is loads_enabled
    if stores_enabled:
        assert stores[(use, "x")] == {definition}
    if loads_enabled:
        assert loads[definition] == {use}


@pytest.mark.parametrize("targets", [("x",), ("x", "y")])
def test_reaching_definitions_kill_only_fully_overwritten_statement(
    targets: tuple[str, ...],
) -> None:
    definition = _copy(targets, "seed")
    overwrite = _copy(("x",), "other")
    use = _copy(("result",), targets[-1])
    block = BaseBlock(1, [overwrite, use])
    graph = _Graph((BaseBlock(0), block, BaseBlock(2)), ((0, 1), (1, 2)))
    graph.stmts.add(definition)
    scope = _install(graph)
    analysis = ReachingDefinitionAnalysis(
        scope,
        graph,
        AnalysisConfig(
            "rd_review", "ReachingDefinitionAnalysis", options={"type": "dataflow analysis"}
        ),
    )
    incoming: set[IRStatement] = {definition}
    assert definition.get_stores() == set(targets)

    outgoing = analysis.transfer_node(block, incoming)

    assert incoming == {definition}
    assert overwrite in outgoing
    assert (definition in outgoing) is (len(targets) > 1)
