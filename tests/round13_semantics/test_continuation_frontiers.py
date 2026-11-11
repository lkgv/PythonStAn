"""Source-grounded continuation obligations through the production inspector.

LangServe APIHandler.stream's nested async generator consumes runnable.astream
with async-for (api_handler.py:1177-1209, pinned0.3.3). These reduced protocol
witnesses preserve that async producer/consumer boundary without importing or
executing the upstream package. Creation must stay dormant.
"""
import ast
import hashlib
from pathlib import Path

import pytest

from tests.round12_semantics.test_descriptor_native import _analyze, _edges, _list_strings


PREFIX = '''events = []
def chosen():
    events.append("chosen")
    return "chunk"
'''

ASYNC_FOR = '''async def produce(callback):
    yield callback()
async def consume(callback):
    async for chunk in produce(callback):
        events.append(chunk)
pending = consume(chosen)
try:
    pending.send(None)
except StopIteration:
    pass
'''

CUSTOM_ASYNC_FOR = '''class Stream:
    def __init__(self, callback):
        self.callback = callback
        self.done = False
    def __aiter__(self):
        events.append("aiter")
        return self
    async def __anext__(self):
        if self.done:
            raise StopAsyncIteration
        self.done = True
        return self.callback()
async def consume(callback):
    async for chunk in Stream(callback):
        events.append(chunk)
pending = consume(chosen)
try:
    pending.send(None)
except StopIteration:
    pass
'''

DIRECT_ANEXT = '''async def produce(callback):
    yield callback()
pending = produce(chosen)
step = pending.__anext__()
before = len(events)
try:
    step.send(None)
except StopIteration as completed:
    result = completed.value
'''


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("intrinsics", [False, True])
def test_langserve_style_async_generator_consumer_retains_chunk_and_callback(tmp_path, policy, intrinsics):
    source = PREFIX + ASYNC_FOR
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["chosen", "chunk"]
    bundle = _analyze(tmp_path, source, context_policy=policy, enabled=intrinsics)
    assert ("subject.produce", "subject.chosen") in _edges(bundle)
    assert {"chosen", "chunk"} <= _list_strings(bundle, "events")


def test_async_for_uses_async_type_protocol_and_awaits_each_advance(tmp_path):
    source = PREFIX + CUSTOM_ASYNC_FOR
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["aiter", "chosen", "chunk"]
    bundle = _analyze(tmp_path, source)
    required = {("subject.consume", "subject.Stream.__aiter__"),
        ("subject.consume", "subject.Stream.__anext__"),
        ("subject.Stream.__anext__", "subject.chosen")}
    assert required <= _edges(bundle)
    assert {"aiter", "chosen", "chunk"} <= _list_strings(bundle, "events")


def test_native_anext_awaitable_creation_is_lazy_and_send_transfers_yield(tmp_path):
    source = PREFIX + DIRECT_ANEXT
    concrete = {}
    exec(source, concrete)
    assert concrete["before"] == 0
    assert concrete["result"] == "chunk"
    assert concrete["events"] == ["chosen"]
    bundle = _analyze(tmp_path, source)
    assert ("subject.produce", "subject.chosen") in _edges(bundle)
    assert "chosen" in _list_strings(bundle, "events")


@pytest.mark.parametrize("suffix", ["", "iterator = pending.__aiter__()", "step = pending.__anext__()"])
def test_async_generator_creation_and_protocol_wrapper_creation_stay_dormant(tmp_path, suffix):
    source = PREFIX + '''async def produce(callback):
    yield callback()
pending = produce(chosen)
''' + suffix + "\n"
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == []
    bundle = _analyze(tmp_path, source)
    assert ("subject.produce", "subject.chosen") not in _edges(bundle)
    assert _list_strings(bundle, "events") == set()


def test_implicit_async_for_bypasses_all_source_iterator_globals(tmp_path):
    shadows = "".join(f'def {name}(*args):\n    events.append("poison")\n    return []\n'
        for name in ("iter", "next", "aiter", "anext"))
    source = PREFIX + shadows + ASYNC_FOR
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["chosen", "chunk"]
    bundle = _analyze(tmp_path, source)
    edges = _edges(bundle)
    assert ("subject.produce", "subject.chosen") in edges
    assert not any(callee in {"subject.iter", "subject.next", "subject.aiter", "subject.anext"}
                   for _, callee in edges)
    assert "poison" not in _list_strings(bundle, "events")


def test_async_for_special_lookup_bypasses_instance_protocol_shadows(tmp_path):
    body = CUSTOM_ASYNC_FOR.replace("    async for chunk in Stream(callback):", '''    stream = Stream(callback)
    stream.__aiter__ = poison
    stream.__anext__ = poison
    async for chunk in stream:''')
    source = PREFIX + 'def poison():\n    events.append("poison")\n' + body
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["aiter", "chosen", "chunk"]
    bundle = _analyze(tmp_path, source)
    assert ("subject.Stream.__anext__", "subject.chosen") in _edges(bundle)
    assert ("subject.consume", "subject.poison") not in _edges(bundle)
    assert "poison" not in _list_strings(bundle, "events")


def test_async_iteration_custom_descriptors_return_unbound_zero_argument_callables(tmp_path):
    source = PREFIX + '''state = {"done": False}
async def advance():
    if state["done"]:
        raise StopAsyncIteration
    state["done"] = True
    return chosen()
def get_iterator():
    return stream
class IterDescriptor:
    def __get__(self, instance, owner):
        events.append("get-iter")
        return get_iterator
class NextDescriptor:
    def __get__(self, instance, owner):
        events.append("get-next")
        return advance
class Stream:
    __aiter__ = IterDescriptor()
    __anext__ = NextDescriptor()
stream = Stream()
async def consume():
    async for chunk in stream:
        events.append(chunk)
pending = consume()
try:
    pending.send(None)
except StopIteration:
    pass
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["get-iter", "get-next", "chosen", "chunk", "get-next"]
    bundle = _analyze(tmp_path, source)
    required = {("subject.consume", "subject.IterDescriptor.__get__"),
        ("subject.consume", "subject.NextDescriptor.__get__"),
        ("subject.consume", "subject.get_iterator"),
        ("subject.consume", "subject.advance"), ("subject.advance", "subject.chosen")}
    assert required <= _edges(bundle)
    assert {"get-iter", "get-next", "chosen", "chunk"} <= _list_strings(bundle, "events")


@pytest.mark.parametrize("driven", [False, True])
def test_asend_value_reaches_body_only_when_lazy_step_is_driven(tmp_path, driven):
    source = PREFIX + '''async def produce():
    callback = yield "ready"
    yield callback()
pending = produce()
first = pending.__anext__()
try:
    first.send(None)
except StopIteration:
    pass
second = pending.asend(chosen)
'''
    if driven:
        source += '''try:
    second.send(None)
except StopIteration:
    pass
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == (["chosen"] if driven else [])
    bundle = _analyze(tmp_path, source)
    if driven:
        assert ("subject.produce", "subject.chosen") in _edges(bundle)
        assert "chosen" in _list_strings(bundle, "events")
    else:
        assert ("subject.produce", "subject.chosen") not in _edges(bundle)
        assert _list_strings(bundle, "events") == set()


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_active_async_generator_retains_late_callback_arguments(tmp_path, policy):
    source = PREFIX + '''async def produce(callback):
    yield callback()
def drive(callback):
    stream = produce(callback)
    step = stream.__anext__()
    try:
        step.send(None)
    except (StopIteration, TypeError):
        pass
for callback in [None, chosen]:
    drive(callback)
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["chosen"]
    bundle = _analyze(tmp_path, source, context_policy=policy)
    assert ("subject.produce", "subject.chosen") in _edges(bundle)
    assert "chosen" in _list_strings(bundle, "events")


@pytest.mark.parametrize("drive", ["next(pending)", "iter(pending)", "await pending"])
def test_invalid_sync_or_coroutine_drive_does_not_activate_async_generator(tmp_path, drive):
    source = PREFIX + '''async def produce(callback):
    yield callback()
pending = produce(chosen)
async def invalid():
    try:
        DRIVE
    except TypeError:
        pass
bad = invalid()
try:
    bad.send(None)
except StopIteration:
    pass
'''.replace("DRIVE", drive)
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == []
    bundle = _analyze(tmp_path, source)
    assert ("subject.produce", "subject.chosen") not in _edges(bundle)
    assert _list_strings(bundle, "events") == set()


def test_async_for_keeps_none_as_a_yielded_value(tmp_path):
    source = PREFIX + '''async def produce():
    yield None
async def consume():
    async for chunk in produce():
        chosen()
pending = consume()
try:
    pending.send(None)
except StopIteration:
    pass
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["chosen"]
    bundle = _analyze(tmp_path, source)
    assert ("subject.consume", "subject.chosen") in _edges(bundle)
    assert "chosen" in _list_strings(bundle, "events")


def test_mixed_late_native_step_target_replays_callback_and_yield_result(tmp_path):
    """Controlled abstract-input extension, not a second concrete step claim."""
    from tests.round12_semantics.test_continuations import analyze, edges, event_values
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan.analysis.pointer.kcfa.heap_model import Field, FieldKind
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle

    source = PREFIX + '''def other():
    events.append("other")
    return "late-chunk"
async def produce(callback):
    yield callback()
async def consume(step):
    chunk = await step
    events.append(chunk)
left = produce(chosen)
right = produce(other)
step = left.__anext__()
pending = consume(step)
try:
    pending.send(None)
except StopIteration:
    pass
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["chosen", "chunk"]
    before, solver = analyze(tmp_path, source, retain=True)
    assert {"chosen", "chunk"} <= event_values(before)
    assert "other" not in event_values(before)
    locations = {node.content.name: node for node in solver.state._env
        if isinstance(node, Ctx) and isinstance(node.content, Variable)
        and node.content.kind.value == "global"}
    wrapper = next(iter(solver.state.get_points_to(locations["step"])))
    target = solver.state.get_field(locations["step"].scope, locations["step"].context,
        wrapper, Field(FieldKind.CLASS_METADATA, "async_generator.resume_target"))
    solver.handle_new_points_to(target, target.scope, solver.state.get_points_to(locations["right"]))
    solver.solve_to_fixpoint()
    after = load_bundle(write_bundle(capture(solver), tmp_path / "after")).data["snapshot"]
    assert ("produce", "other") in edges(after)
    assert {"other", "late-chunk"} <= event_values(after)


def test_explicit_native_aiter_anext_await_transfers_yield(tmp_path):
    source = PREFIX + '''async def produce(callback):
    yield callback()
async def consume():
    iterator = aiter(produce(chosen))
    chunk = await anext(iterator)
    events.append(chunk)
pending = consume()
try:
    pending.send(None)
except StopIteration:
    pass
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["chosen", "chunk"]
    bundle = _analyze(tmp_path, source)
    assert ("subject.produce", "subject.chosen") in _edges(bundle)
    assert {"chosen", "chunk"} <= _list_strings(bundle, "events")


@pytest.mark.parametrize("invalid", ["pending.asend()", "pending.__anext__(None)", "step.send()"])
def test_known_invalid_async_advance_arguments_do_not_activate_body(tmp_path, invalid):
    source = PREFIX + '''async def produce():
    yield chosen()
pending = produce()
step = pending.__anext__()
async def invalid():
    try:
        await INVALID
    except TypeError:
        pass
bad = invalid()
try:
    bad.send(None)
except StopIteration:
    pass
'''.replace("INVALID", invalid)
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == []
    bundle = _analyze(tmp_path, source)
    assert ("subject.produce", "subject.chosen") not in _edges(bundle)
    assert _list_strings(bundle, "events") == set()


def test_custom_await_can_return_native_async_generator_advance_iterator(tmp_path):
    source = PREFIX + '''async def produce():
    yield chosen()
class Awaitable:
    def __await__(self):
        return produce().__anext__()
async def consume():
    chunk = await Awaitable()
    events.append(chunk)
pending = consume()
try:
    pending.send(None)
except StopIteration:
    pass
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["chosen", "chunk"]
    bundle = _analyze(tmp_path, source)
    assert ("subject.produce", "subject.chosen") in _edges(bundle)
    assert {"chosen", "chunk"} <= _list_strings(bundle, "events")


def test_prefect_style_yield_from_done_container_keeps_future_result_callback(tmp_path):
    source = PREFIX + '''class Future:
    def result(self):
        return chosen()
def as_completed(done):
    yield from done
pending = as_completed({Future()})
future = pending.send(None)
result = future.result()
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == "chunk"
    assert concrete["events"] == ["chosen"]
    bundle = _analyze(tmp_path, source)
    assert ("subject.Future.result", "subject.chosen") in _edges(bundle)
    assert "chosen" in _list_strings(bundle, "events")


def test_native_yield_from_separates_yielded_values_and_return_callback(tmp_path):
    source = PREFIX + '''def poison():
    events.append("poison")
def child():
    yield poison
    return chosen
def parent():
    callback = yield from child()
    callback()
    yield "tail"
pending = parent()
first = pending.send(None)
second = pending.send(None)
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["first"] is concrete["poison"]
    assert concrete["second"] == "tail"
    assert concrete["events"] == ["chosen"]
    bundle = _analyze(tmp_path, source)
    edges = _edges(bundle)
    assert ("subject.parent", "subject.chosen") in edges
    assert ("subject.parent", "subject.poison") not in edges
    assert "chosen" in _list_strings(bundle, "events")


def test_yield_from_parent_creation_stays_dormant(tmp_path):
    source = PREFIX + '''def child():
    chosen()
    yield 1
def parent():
    yield from child()
pending = parent()
iterator = iter(pending)
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == []
    bundle = _analyze(tmp_path, source)
    assert ("subject.child", "subject.chosen") not in _edges(bundle)
    assert _list_strings(bundle, "events") == set()


@pytest.mark.parametrize("kind,expected", [(ast.YieldFrom, True), (ast.Yield, False)])
def test_ir_yield_preserves_original_delegation_flag(kind, expected):
    from pythonstan.ir.ir_statements import IRYield
    statement = ast.Expr(value=kind(value=ast.Name(id="source", ctx=ast.Load())))
    ir = IRYield(statement)
    assert ir.is_yield_from() is expected
    assert isinstance(ir.value, ast.Name)


@pytest.mark.parametrize("path,line,digest,expression", [
    ("futures.py", 644, "b1bb443abc327a01262168c41da9e2755f8871820282ba1d1c5f2d48e9b274b2", "(yield from done)"),
    ("utilities/collections.py", 111, "06cea573647f07c55e34a429371137e59e531bc647806e1cdae5e0485d5810d3",
     "(yield from flatten(cast(NestedDict[KT, VT], v), _parent=k_parent))")])
def test_pinned_prefect_delegation_source_binding(path, line, digest, expression):
    root = Path(__file__).resolve().parents[2].parent / "round8_corpus/prefect/prefect-3.9.1/src/prefect"
    source = root / path
    if not source.exists():
        pytest.skip("rehydrate exact pinned Prefect source for its separate source-binding check")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    tree = ast.parse(source.read_bytes())
    assert any(isinstance(node, ast.YieldFrom) and node.lineno == line
               and ast.unparse(node) == expression for node in ast.walk(tree))


def test_yield_from_native_protocol_bypasses_shadowed_iter_next(tmp_path):
    source = PREFIX + '''def iter(*args):
    events.append("poison")
def next(*args):
    events.append("poison")
def child():
    yield chosen
def parent():
    yield from child()
pending = parent()
callback = pending.send(None)
callback()
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["chosen"]
    bundle = _analyze(tmp_path, source)
    assert ("subject", "subject.chosen") in _edges(bundle)
    assert not any(callee in {"subject.iter", "subject.next"} for _, callee in _edges(bundle))
    assert "poison" not in _list_strings(bundle, "events")


def test_yield_from_default_none_completion_and_forwarded_send_value(tmp_path):
    source = PREFIX + '''completion = "unset"
def child():
    callback = yield None
    callback()
def parent():
    global completion
    completion = yield from child()
    yield "tail"
pending = parent()
first = pending.send(None)
second = pending.send(chosen)
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["first"] is None
    assert concrete["second"] == "tail"
    assert concrete["completion"] is None
    assert concrete["events"] == ["chosen"]
    bundle = _analyze(tmp_path, source)
    assert ("subject.child", "subject.chosen") in _edges(bundle)
    snapshot = bundle.data["snapshot"]
    entities = {entity["id"]: entity for entity in snapshot["entities"]}
    slots = {entity["id"] for entity in entities.values()
        if entity["kind"] == "ctx" and entity["attributes"].get("name") == "completion"
        and entity["attributes"].get("variable_kind") == "global"}
    assert any(row["owner"] in slots and "value" in entities[row["object"]]["attributes"]
               and entities[row["object"]]["attributes"]["value"] is None
               for row in snapshot["views"]["ctx_points"]["rows"])


@pytest.mark.parametrize("channel", ["element", "completion"])
def test_delegation_retains_late_element_and_terminal_callback_facts(tmp_path, channel):
    """Monotone abstract-input extension, not exact exhausted-state replay."""
    from tests.round12_semantics.test_continuations import analyze, edges, event_values
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan.analysis.pointer.kcfa.heap_model import Field, FieldKind, elem
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle

    if channel == "element":
        body = '''bucket = [chosen]
def parent():
    yield from bucket
pending = parent()
callback = pending.send(None)
callback()
'''
        global_name = "bucket"
        field = elem()
    else:
        body = '''def child():
    yield 1
    return chosen
def parent(iterator):
    callback = yield from iterator
    callback()
    yield 2
inner = child()
pending = parent(inner)
pending.send(None)
pending.send(None)
'''
        global_name = "inner"
        field = Field(FieldKind.CLASS_METADATA, "generator.completion_value")
    source = PREFIX + '''def other():
    events.append("other")
''' + body
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["chosen"]
    before, solver = analyze(tmp_path, source, retain=True)
    assert "other" not in event_values(before)
    locations = {node.content.name: node for node in solver.state._env
        if isinstance(node, Ctx) and isinstance(node.content, Variable)
        and node.content.kind.value == "global"}
    owner = next(iter(solver.state.get_points_to(locations[global_name])))
    target = solver.state.get_field(locations[global_name].scope,
        locations[global_name].context, owner, field)
    solver.handle_new_points_to(target, target.scope, solver.state.get_points_to(locations["other"]))
    solver.solve_to_fixpoint()
    after = load_bundle(write_bundle(capture(solver), tmp_path / "after")).data["snapshot"]
    caller = "<module>" if channel == "element" else "parent"
    assert (caller, "other") in edges(after)
    assert "other" in event_values(after)


def test_custom_delegation_uses_descriptor_protocol_and_bypasses_instance_shadows(tmp_path):
    source = PREFIX + '''def poison(*args):
    events.append("poison")
def get_iterator():
    return iterator
def advance():
    return chosen
class IterDescriptor:
    def __get__(self, instance, owner):
        events.append("iter-get")
        return get_iterator
class NextDescriptor:
    def __get__(self, instance, owner):
        events.append("next-get")
        return advance
class Iterator:
    __next__ = NextDescriptor()
class Container:
    __iter__ = IterDescriptor()
iterator = Iterator()
iterator.__next__ = poison
container = Container()
container.__iter__ = poison
def parent():
    yield from container
pending = parent()
callback = pending.send(None)
callback()
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["iter-get", "next-get", "chosen"]
    bundle = _analyze(tmp_path, source)
    assert ("subject", "subject.chosen") in _edges(bundle)
    assert {"iter-get", "next-get", "chosen"} <= _list_strings(bundle, "events")
    assert "poison" not in _list_strings(bundle, "events")


def test_late_custom_iter_result_replays_existing_delegated_next_consumer(tmp_path):
    """A real heap producer extension, not an injected callback edge."""
    from tests.round12_semantics.test_continuations import analyze, edges, event_values
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan.analysis.pointer.kcfa.heap_model import attr
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle

    source = PREFIX + '''def other():
    events.append("other")
class Iterator:
    def __init__(self, callback): self.callback = callback
    def __next__(self): return self.callback
class Holder:
    def __init__(self, iterator): self.iterator = iterator
class Container:
    def __iter__(self): return holder.iterator
first = Iterator(chosen)
second = Iterator(other)
holder = Holder(first)
def parent():
    yield from Container()
pending = parent()
callback = pending.send(None)
callback()
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["chosen"]
    before, solver = analyze(tmp_path, source, retain=True)
    assert "other" not in event_values(before)
    locations = {node.content.name: node for node in solver.state._env
        if isinstance(node, Ctx) and isinstance(node.content, Variable)
        and node.content.kind.value == "global"}
    owner = next(iter(solver.state.get_points_to(locations["holder"])))
    target = solver.state.get_field(locations["holder"].scope,
        locations["holder"].context, owner, attr("iterator"))
    solver.handle_new_points_to(target, target.scope, solver.state.get_points_to(locations["second"]))
    solver.solve_to_fixpoint()
    after = load_bundle(write_bundle(capture(solver), tmp_path / "after")).data["snapshot"]
    assert ("<module>", "other") in edges(after)
    assert "other" in event_values(after)


def test_dictionary_delegation_keeps_native_keys_separate_and_provenance_open(tmp_path):
    from tests.round12_semantics.test_continuations import analyze, event_values, values
    source = PREFIX + '''def parent():
    yield from {"key": chosen}
pending = parent()
value = pending.send(None)
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["value"] == "key"
    data, solver = analyze(tmp_path, source, retain=True)
    assert event_values(data) == set()
    entities = {entity["id"]: entity for entity in data["entities"]}
    assert any(entities[obj]["attributes"].get("value") == "key" for obj in values(data, "value"))
    assert any("native_mapping_key_provenance" in row.get("effects", ())
               and "iteration_order" in row.get("effects", ())
               for row in solver.state._residual_effects.values())


def test_yield_from_coroutine_iterator_separates_suspension_and_completion(tmp_path):
    source = PREFIX + '''def suspension_callback():
    events.append("suspension")
class Pause:
    def __await__(self):
        yield suspension_callback
        return "resumed"
async def child():
    await Pause()
    return chosen
def parent():
    callback = yield from child().__await__()
    callback()
    yield "tail"
pending = parent()
first = pending.send(None)
first()
second = pending.send(None)
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["first"] is concrete["suspension_callback"]
    assert concrete["second"] == "tail"
    assert concrete["events"] == ["suspension", "chosen"]
    bundle = _analyze(tmp_path, source)
    edges = _edges(bundle)
    assert {("subject", "subject.suspension_callback"),
            ("subject.parent", "subject.chosen")} <= edges
    assert ("subject", "subject.chosen") not in edges
    assert {"suspension", "chosen"} <= _list_strings(bundle, "events")


@pytest.mark.parametrize("kind", ["async-generator-step", "coroutine", "coroutine-iterator"])
def test_async_step_send_value_reaches_inner_await_continuation(tmp_path, kind):
    source = PREFIX + '''class Pause:
    def __await__(self):
        callback = yield "pause"
        callback()
        return None
async def produce():
    await Pause()
    yield "done"
pending = produce()
step = pending.__anext__()
first = step.send(None)
try:
    step.send(chosen)
except StopIteration:
    pass
'''
    if kind != "async-generator-step":
        source = source.replace('    yield "done"', '    return "done"')
        source = source.replace("step = pending.__anext__()",
            "step = pending" if kind == "coroutine" else "step = pending.__await__()")
    concrete = {}
    exec(source, concrete)
    assert concrete["first"] == "pause"
    assert concrete["events"] == ["chosen"]
    bundle = _analyze(tmp_path, source)
    assert ("subject.Pause.__await__", "subject.chosen") in _edges(bundle)
    assert "chosen" in _list_strings(bundle, "events")


def test_late_send_argument_producer_replays_through_existing_await(tmp_path):
    from tests.round12_semantics.test_continuations import analyze, edges, event_values
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan.analysis.pointer.kcfa.heap_model import elem
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle
    source = PREFIX + '''class Pause:
    def __await__(self):
        callback = yield "pause"
        callback()
        return None
async def produce():
    await Pause()
    yield "done"
pending = produce()
step = pending.__anext__()
requests = [None]
for value in requests:
    try:
        step.send(value)
    except StopIteration:
        pass
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == []
    variant = {}
    exec(source.replace("requests = [None]", "requests = [None, chosen]"), variant)
    assert variant["events"] == ["chosen"]
    before, solver = analyze(tmp_path, source, retain=True)
    assert "chosen" not in event_values(before)
    locations = {node.content.name: node for node in solver.state._env
        if isinstance(node, Ctx) and isinstance(node.content, Variable)
        and node.content.kind.value == "global"}
    owner = next(iter(solver.state.get_points_to(locations["requests"])))
    target = solver.state.get_field(locations["requests"].scope,
        locations["requests"].context, owner, elem())
    solver.handle_new_points_to(target, target.scope, solver.state.get_points_to(locations["chosen"]))
    solver.solve_to_fixpoint()
    after = load_bundle(write_bundle(capture(solver), tmp_path / "after")).data["snapshot"]
    assert ("Pause.__await__", "chosen") in edges(after)
    assert "chosen" in event_values(after)


def test_implicit_next_none_reaches_await_generator_yield_expression(tmp_path):
    source = '''events = []
class Pause:
    def __await__(self):
        received = yield "pause"
        events.append(received)
async def produce():
    await Pause()
pending = produce()
iterator = pending.__await__()
next(iterator)
try:
    next(iterator)
except StopIteration:
    pass
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == [None]
    bundle = _analyze(tmp_path, source)
    snapshot = bundle.data["snapshot"]
    entities = {entity["id"]: entity for entity in snapshot["entities"]}
    globals_ = {entity["id"] for entity in entities.values()
        if entity["kind"] == "ctx" and entity["attributes"].get("name") == "events"}
    lists = {row["object"] for row in snapshot["views"]["ctx_points"]["rows"] if row["owner"] in globals_}
    elements = {entity["id"] for entity in entities.values()
        if entity["kind"] == "ctx" and entity["attributes"].get("field_kind") == "elem"
        and entity["links"].get("object", {}).get("ref") in lists}
    assert any(row["owner"] in elements and "value" in entities[row["object"]]["attributes"]
               and entities[row["object"]]["attributes"]["value"] is None
               for row in snapshot["views"]["ctx_points"]["rows"])


def test_late_await_receiver_retains_already_known_send_payload(tmp_path):
    from tests.round12_semantics.test_continuations import analyze, event_values
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle
    source = PREFIX + '''class Pause:
    def __init__(self, tag): self.tag = tag
    def __await__(self):
        callback = yield self.tag
        events.append(self.tag)
        callback()
        return None
first = Pause("first")
second = Pause("second")
incoming = first
async def produce():
    await incoming
pending = produce()
pending.send(None)
try:
    pending.send(chosen)
except StopIteration:
    pass
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["first", "chosen"]
    variant = {}
    exec(source.replace("incoming = first", "incoming = second"), variant)
    assert variant["events"] == ["second", "chosen"]
    before, solver = analyze(tmp_path, source, retain=True)
    assert "second" not in event_values(before)
    locations = {node.content.name: node for node in solver.state._env
        if isinstance(node, Ctx) and isinstance(node.content, Variable)
        and node.content.kind.value == "global"}
    solver.handle_new_points_to(locations["incoming"], locations["incoming"].scope,
                               solver.state.get_points_to(locations["second"]))
    solver.solve_to_fixpoint()
    after = load_bundle(write_bundle(capture(solver), tmp_path / "after")).data["snapshot"]
    assert {"second", "chosen"} <= event_values(after)
