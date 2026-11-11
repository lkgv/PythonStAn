"""Guard discarded-copy cost without changing represented control flow."""
import ast
import copy

import pytest

from benchmark.round9_frontend.compare_cfg_copy import graph_signature
from pythonstan.analysis.transform.cfg import CFGTransformer
from pythonstan.graph.cfg import BaseBlock, ExceptionEdge, IfEdge, NormalEdge, ReturnEdge
from pythonstan.ir import IRModule
from pythonstan.world import World
from pythonstan.world.config import Config
from pythonstan.world.analysis_manager import AnalysisManager


def test_edge_copy_keeps_canonical_endpoints_and_independent_annotation():
    class CanonicalStatement:
        def __deepcopy__(self, memo):
            raise AssertionError("Canonical endpoint statement must not be copied")

    source = BaseBlock(0, [CanonicalStatement()])
    target = BaseBlock(1, [CanonicalStatement()])
    shared = ["ValueError", {"policy": ["cleanup"]}]
    edge = ExceptionEdge(source, target, {"first": shared, "alias": shared})
    result = CFGTransformer._copy_edge(edge)
    assert result is not edge and result.src is source and result.tgt is target
    assert result.e == edge.e and result.e is not edge.e
    assert result.e["first"] is result.e["alias"]
    result.e["first"][1]["policy"].append("other")
    assert edge.e["first"][1]["policy"] == ["cleanup"]


@pytest.mark.parametrize("edge_kind", ["normal", "true", "false"])
def test_typed_edge_attributes_and_source_graph_are_not_modified(edge_kind):
    source, target = BaseBlock(1, []), BaseBlock(2, [])
    edge = NormalEdge(source, target) if edge_kind == "normal" else IfEdge(
        source, target, ast.Name(id="flag", ctx=ast.Load()), edge_kind == "true")
    result = CFGTransformer._copy_edge(edge)
    replacement = BaseBlock(3, [])
    result.src = replacement; result.tgt = replacement
    assert edge.src is source and edge.tgt is target
    assert type(result) is type(edge)
    assert {k:v for k,v in vars(result).items() if k not in {"src", "tgt"}} == {
        k:v for k,v in vars(edge).items() if k not in {"src", "tgt"}}


@pytest.mark.parametrize("subclass", [False, True])
def test_payload_or_extension_edge_keeps_legacy_endpoint_alias_copy(subclass):
    class Extension(NormalEdge):
        def __init__(self, source, target):
            super().__init__(source, target)
            self.call_site = source
    source, target = BaseBlock(1, []), BaseBlock(2, [])
    edge = Extension(source, target) if subclass else ReturnEdge(source, target, source, set())
    result = CFGTransformer._copy_edge(edge)
    assert result.src is not source and result.tgt is not target
    assert result.call_site is result.src
    copied_site = result.call_site
    result.src = BaseBlock(3, []); result.tgt = BaseBlock(4, [])
    assert result.call_site is copied_site
    assert edge.call_site is source


SOURCES = [
    """
def plain(x):
    if x:
        return first(x)
    else:
        return second(x)
answer = plain(flag)
""",
    """
class Box:
    def nested(self, values):
        def read(x):
            return x
        for value in values:
            if skip(value):
                continue
            if stop(value):
                break
            read(value)
        else:
            finished()
        return read
""",
    """
def guarded(value):
    try:
        if value:
            raise ValueError(value)
        work(value)
    except ValueError as err:
        recover(err)
    except TypeError:
        other()
    else:
        normal()
    finally:
        cleanup()
""",
    """
async def pending(manager, values):
    async with manager as active:
        while await active.next():
            value = await active.read()
            if value:
                yield value
            else:
                break
    return
""",
]


@pytest.mark.parametrize("source", SOURCES)
def test_entire_frontend_cfg_matches_legacy_copy_strategy(tmp_path, monkeypatch, source):
    path = tmp_path / "subject.py"; path.write_text(source)
    World.setup(); world = World()
    world.build(Config(str(path), str(tmp_path), mock_libs=False))
    manager = AnalysisManager(); manager.build([]); world.analysis_manager = manager
    module = IRModule("subject", ast.parse(source), "subject", str(path))
    world.scope_manager.scopes.add(module); world.scope_manager.names2scope["subject"] = module
    for phase in ("three address", "ir", "block cfg"):
        manager.analysis(phase, module)
    optimized = CFGTransformer._copy_edge
    with monkeypatch.context() as patch:
        patch.setattr(CFGTransformer, "_copy_edge", staticmethod(copy.deepcopy))
        manager.analysis("cfg", module)
        legacy = graph_signature(world, module)
    assert CFGTransformer._copy_edge is optimized
    manager.analysis("cfg", module)
    current = graph_signature(world, module)
    assert current == legacy
    assert current["canonical_statement_identity_preserved"]
