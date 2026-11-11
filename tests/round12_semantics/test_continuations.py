"""Creation/resumption counterexamples through source -> engine -> inspector.

These assert finite heap/call may facts, not concrete reachability, sequencing,
exception routing, or complete coroutine protocol soundness.
"""
from pathlib import Path

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.world.pipeline import Pipeline
from pythonstan_inspector.adapter import capture
from pythonstan_inspector.storage import load_bundle, write_bundle

ROOT = Path(__file__).resolve().parents[2]


def analyze(tmp_path, source, *, policy="1-cfa", intrinsics=False, retain=False):
    filename = tmp_path / "subject.py"
    filename.write_text(source)
    config = Config(context_policy=policy, max_iterations=50000,
        work_scheduling_policy="fair", enable_ai_intrinsics=intrinsics,
        enable_synthetic_method_contexts=False, entry_points=[], log_level="ERROR")
    pipeline = Pipeline(config={"filename": str(filename), "project_path": str(tmp_path),
        "library_paths": [], "import_level": 0, "mock_libs": False, "time_count": False,
        "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                      "options": config.to_dict()}]})
    solver = pipeline.analysis_manager.get_analyzer("pta").solver
    solver.enable_inspector_boundaries()
    pipeline.run()
    assert solver._stats["converged"] == 1
    assert solver._stats["pending_work"] == 0
    snapshot = capture(solver)
    bundle = load_bundle(write_bundle(snapshot, tmp_path / "bundle"))
    data = bundle.data["snapshot"]
    assert data["boundary"]["coherent"] is True
    for view in data["views"].values():
        assert view["coverage"]["registry"] == "complete"
    return (data, solver) if retain else data


def normalized(name):
    return "<module>" if name == "subject" else name.removeprefix("subject.")


def edges(data):
    entities = {entity["id"]: entity for entity in data["entities"]}
    result = set()
    for row in data["views"]["call_edges"]["rows"]:
        site = entities[row["callsite"]]
        caller = entities[site["links"]["scope"]["ref"]]
        callee = entities[row["callee"]]
        result.add((normalized(caller["attributes"]["qualname"]),
                    normalized(callee["attributes"]["qualname"])))
    return result


def values(data, variable):
    entities = {entity["id"]: entity for entity in data["entities"]}
    owners = {entity["id"] for entity in data["entities"] if entity["kind"] == "ctx"
        and entity["attributes"].get("name") == variable
        and entity["attributes"].get("variable_kind") == "global"}
    return {row["object"] for row in data["views"]["ctx_points"]["rows"] if row["owner"] in owners}


def event_values(data):
    entities = {entity["id"]: entity for entity in data["entities"]}
    containers = values(data, "events")
    owners = {entity["id"] for entity in data["entities"] if entity["kind"] == "ctx"
        and entity["attributes"].get("content_kind") == "field"
        and entity["attributes"].get("field_kind") == "elem"
        and entity["links"]["object"]["ref"] in containers}
    return {entities[row["object"]]["attributes"]["value"]
        for row in data["views"]["ctx_points"]["rows"] if row["owner"] in owners
        and "value" in entities[row["object"]]["attributes"]}


def test_langserve_creation_does_not_execute_body(tmp_path):
    source = (ROOT / "benchmark/round11_applications/sources/langserve_endpoint_dormant.py").read_text()
    data = analyze(tmp_path, source)
    assert ("add_routes.invoke", "APIHandler.invoke") not in edges(data)
    assert ("APIHandler.invoke", "handler_effect") not in edges(data)
    assert event_values(data) == set()


def test_langserve_explicit_send_preserves_closure_receiver_and_effect(tmp_path):
    source = (ROOT / "benchmark/round11_applications/sources/langserve_endpoint_resumed.py").read_text()
    data = analyze(tmp_path, source)
    assert ("add_routes.invoke", "APIHandler.invoke") in edges(data)
    assert ("APIHandler.invoke", "handler_effect") in edges(data)
    assert "handled" in event_values(data)


BODY = '''
events = []
def chosen():
    events.append("chosen")
    return "value"
def unrelated():
    events.append("unrelated")
    return "other"
'''


@pytest.mark.parametrize("kind", ["generator", "coroutine"])
@pytest.mark.parametrize("extra", ["", "iterator = iter(pending)"])
def test_dormant_body_keeps_heap_effects_unpublished(tmp_path, kind, extra):
    if kind == "generator":
        factory = "def produce(callback):\n    callback()\n    yield 1\n"
    else:
        if extra:
            extra = "iterator = pending.__await__()"
        factory = "async def produce(callback):\n    return callback()\n"
    data = analyze(tmp_path, BODY + factory + "pending = produce(chosen)\n" + extra + "\n")
    assert event_values(data) == set(), "creation/iterator construction published body heap effects"
    assert ("produce", "chosen") not in edges(data)
    assert ("produce", "unrelated") not in edges(data)


@pytest.mark.parametrize("advance", ["next(pending)", "pending.__next__()", "pending.send(None)"])
def test_generator_advancement_without_result_binding_keeps_arguments(tmp_path, advance):
    source = BODY + "def produce(callback):\n    callback()\n    yield 1\npending = produce(chosen)\n" + advance + "\n"
    data = analyze(tmp_path, source)
    assert ("produce", "chosen") in edges(data)
    assert "chosen" in event_values(data)
    assert ("produce", "unrelated") not in edges(data)


def test_repeated_send_keeps_future_sent_values(tmp_path):
    source = BODY + '''
def produce():
    callback = yield None
    callback()
    callback = yield None
    callback()
pending = produce()
pending.send(None)
pending.send(chosen)
pending.send(unrelated)
'''
    data = analyze(tmp_path, source)
    assert {("produce", "chosen"), ("produce", "unrelated")} <= edges(data)
    assert {"chosen", "unrelated"} <= event_values(data)


def test_nested_await_activates_inner_body_and_transfers_result(tmp_path):
    source = BODY + '''
async def leaf(callback):
    return callback()
async def outer(callback):
    result = await leaf(callback)
    events.append(result)
    return result
pending = outer(chosen)
pending.send(None)
'''
    data = analyze(tmp_path, source)
    assert {("outer", "leaf"), ("leaf", "chosen")} <= edges(data)
    assert {"chosen", "value"} <= event_values(data)


def test_await_iterator_next_actually_resumes_coroutine(tmp_path):
    source = BODY + '''
async def produce(callback):
    return callback()
pending = produce(chosen)
iterator = pending.__await__()
next(iterator)
'''
    data = analyze(tmp_path, source)
    assert ("produce", "chosen") in edges(data)
    assert "chosen" in event_values(data)


def test_builtin_iter_generator_then_next_resumes(tmp_path):
    source = BODY + "def produce(callback):\n    callback()\n    yield 1\npending = produce(chosen)\niterator = iter(pending)\nnext(iterator)\n"
    data = analyze(tmp_path, source)
    assert ("produce", "chosen") in edges(data)
    assert "chosen" in event_values(data)


def test_native_await_iterator_exposes_send(tmp_path):
    source = "async def produce():\n    return 1\npending = produce()\niterator = pending.__await__()\nadvance = iterator.send\n"
    data = analyze(tmp_path, source)
    assert values(data, "advance"), "native coroutine wrapper.send is a real bound builtin method"


def test_custom_awaitable_protocol_keeps_callbacks(tmp_path):
    source = BODY + '''
class Awaitable:
    def __await__(self):
        chosen()
        yield None
async def outer():
    await Awaitable()
pending = outer()
pending.send(None)
'''
    data = analyze(tmp_path, source)
    assert ("Awaitable.__await__", "chosen") in edges(data)
    assert "chosen" in event_values(data)


def test_next_on_native_coroutine_does_not_run_body(tmp_path):
    source = BODY + "async def produce(callback):\n    return callback()\npending = produce(chosen)\nnext(pending)\n"
    data = analyze(tmp_path, source)
    assert event_values(data) == set()
    assert ("produce", "chosen") not in edges(data)


def test_implicit_await_advancement_bypasses_shadowed_next(tmp_path):
    source = BODY + '''
def next(iterator):
    events.append("shadowed-next")
    return None
class Awaitable:
    def __await__(self):
        chosen()
        yield None
async def outer():
    await Awaitable()
pending = outer()
pending.send(None)
'''
    data = analyze(tmp_path, source)
    assert "chosen" in event_values(data)
    assert "shadowed-next" not in event_values(data)
    assert ("outer", "next") not in edges(data)


def test_custom_await_descriptor_binding_preserves_callback(tmp_path):
    source = BODY + '''
class AwaitDescriptor:
    def __get__(self, instance, owner):
        events.append("bound")
        def protocol():
            chosen()
            yield None
        return protocol
class Awaitable:
    __await__ = AwaitDescriptor()
async def outer():
    await Awaitable()
pending = outer()
pending.send(None)
'''
    data = analyze(tmp_path, source)
    assert {"bound", "chosen"} <= event_values(data)


@pytest.mark.parametrize("indirect", [False, True])
def test_staged_late_generator_fact_replays_activation(tmp_path, indirect):
    """Controlled input-fact extension, not a concrete source execution claim."""
    source = BODY + "def produce(callback):\n    callback()\n    yield 1\nincoming = None\n"
    source += "iterator = iter(incoming)\nnext(iterator)\n" if indirect else "next(incoming)\n"
    source += "pending = produce(chosen)\n"
    before, solver = analyze(tmp_path, source, retain=True)
    assert event_values(before) == set()
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    locations = {node.content.name: node for node in solver.state._env
        if isinstance(node, Ctx) and isinstance(node.content, Variable)
        and node.content.kind.value == "global"}
    pending = solver.state._env[locations["pending"]]
    receiver = locations["incoming"]
    solver.handle_new_points_to(receiver, receiver.scope, pending)
    solver.solve_to_fixpoint()
    after = load_bundle(write_bundle(capture(solver), tmp_path / "after")).data["snapshot"]
    assert ("produce", "chosen") in edges(after)
    assert "chosen" in event_values(after)


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("intrinsics", [False, True])
def test_native_await_wrapper_send_and_bindings_across_modes(tmp_path, policy, intrinsics):
    source = BODY + '''
async def produce(callback):
    return callback()
def build(callback):
    return produce(callback)
first = build(chosen)
first_iterator = first.__await__()
first_iterator.send(None)
second = build(unrelated)
second_iterator = second.__await__()
second_iterator.send(None)
'''
    data = analyze(tmp_path, source, policy=policy, intrinsics=intrinsics)
    assert {("produce", "chosen"), ("produce", "unrelated")} <= edges(data)
    assert {"chosen", "unrelated"} <= event_values(data)
