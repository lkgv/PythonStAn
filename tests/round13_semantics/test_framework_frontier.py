"""Source-bound framework value frontier; no upstream imports or deployment roots.

The executable boundary model is ours. CPython independently establishes its
result/effect; the real production pipeline must recover those may-witnesses.
Pinned upstream files are parsed and hash/AST-bound, never imported or executed.
"""
import ast
import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.round12_semantics.test_continuations import analyze, edges, event_values, values


ROOT = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ.get("PYTHONSTAN_CORPUS_CACHE", ROOT.parent / "round8_corpus"))

API_HASH = "1645456302c9710fe41d746af44cf0c78e8a1748c231701e37c931abb502b83b"
SERVER_HASH = "b94109e55836bc6b674e01b7316a202a024f3a5eda10afab7f2847685a502bb5"
UTILS_HASH = "3f6878056d4d5f1ea3cd60fe87dded805148641fd0d4410ad6165eff856ae469"
MODELS_HASH = "dc4a46a8003677cf398ebd4435df8f8fc7b56fcd2e1983ed3226239d41dba777"

BINDINGS = (
    ("langserve/langserve-0.3.3", "langserve/api_handler.py", API_HASH,
     "langserve.api_handler._unpack_request_config", 216,
     "projected_config = await per_req_config_modifier(projected_config, request)",
     "0872d9e7f89781ce487a2063fbbc4580404fc8b79f84551daa0ebf325bc469e7"),
    ("langserve/langserve-0.3.3", "langserve/api_handler.py", API_HASH,
     "langserve.api_handler.APIHandler.__init__", 643,
     "self._per_req_config_modifier = per_req_config_modifier",
     "77d7ee2f1efd38a8f3e4fc1d693c27d2ca8ed6316e208a7f1ec6d996ea2b0868"),
    ("langserve/langserve-0.3.3", "langserve/api_handler.py", API_HASH,
     "langserve.api_handler.APIHandler._get_config_and_input", 828,
     "per_req_config_modifier=self._per_req_config_modifier",
     "416b780ad710024bd5a78550fc2947d2f3771dc6929d55c8ee29c80fbedadff0"),
    ("langserve/langserve-0.3.3", "langserve/server.py", SERVER_HASH,
     "langserve.server.add_routes", 450,
     "per_req_config_modifier=per_req_config_modifier",
     "950a57bb6675650d918b8b08689cafbe7af94df4c99c2b6b5400487dc153e715"),
    ("fastapi/fastapi-0.162.0", "fastapi/dependencies/utils.py", UTILS_HASH,
     "fastapi.dependencies.utils.solve_dependencies", 684,
     "original_call = sub_dependant.call",
     "27343d0fc2922c528fdb7e71cc5d0e8b4c7ff7b35332a2f4e0a7b9d97255b86b"),
    ("fastapi/fastapi-0.162.0", "fastapi/dependencies/utils.py", UTILS_HASH,
     "fastapi.dependencies.utils.solve_dependencies", 685,
     "call = getattr(dependency_overrides_provider, 'dependency_overrides', {}).get(original_call, original_call)",
     "c9125841d0113fe319389e5dcec6c8614367c94a9406e5d394fbcdf04fcc5e31"),
    ("fastapi/fastapi-0.162.0", "fastapi/dependencies/utils.py", UTILS_HASH,
     "fastapi.dependencies.utils.solve_dependencies", 713,
     "sub_dependant_cache_key = _get_cache_key(dependant=sub_dependant, uses_scopes_cache=_uses_scopes_cache)",
     "ff6f4e8906e2595ca3ea54ff8d0078e2b8d4e63eff0bf0a72ecd32c14f23e68a"),
    ("fastapi/fastapi-0.162.0", "fastapi/dependencies/utils.py", UTILS_HASH,
     "fastapi.dependencies.utils.solve_dependencies", 731,
     "solved = await call(**solved_result.values)",
     "ac2ee055c2b3d86ef83290750f26c2271c16dcdd4e84bfd382e23d34a8a490b2"),
    ("fastapi/fastapi-0.162.0", "fastapi/dependencies/models.py", MODELS_HASH,
     "fastapi.dependencies.models._get_cache_key", 92,
     "return (dependant.call, scopes_for_cache, _get_computed_scope(dependant=dependant) or '')",
     "8fbca01b98455224bd80b0c272693b50d71a9d19672e3ee64dddc860bb85e347"),
)


def _qualified_nodes(tree, module):
    catalog = {}
    scopes = []

    class Visitor(ast.NodeVisitor):
        def generic_visit(self, node):
            line = getattr(node, "lineno", None)
            if line is not None:
                catalog.setdefault((".".join((module, *scopes)), line, ast.unparse(node)), []).append(node)
            super().generic_visit(node)

        def definition(self, node):
            scopes.append(node.name)
            self.generic_visit(node)
            scopes.pop()

        visit_FunctionDef = definition
        visit_AsyncFunctionDef = definition
        visit_ClassDef = definition

    Visitor().visit(tree)
    return catalog


@pytest.mark.parametrize("project,source,file_sha,qualname,line,expression,node_sha", BINDINGS)
def test_pinned_framework_ast_and_content_binding(project, source, file_sha, qualname, line, expression, node_sha):
    path = CACHE / project / source
    if not path.exists():
        pytest.skip("rehydrate the exact frozen round8 corpus to verify upstream AST bindings")
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == file_sha
    tree = ast.parse(raw.decode("utf-8"), filename=source)
    catalog = _qualified_nodes(tree, source.removesuffix(".py").replace("/", "."))
    matches = catalog.get((qualname, line, expression), [])
    assert len(matches) == 1
    assert hashlib.sha256(ast.dump(matches[0], include_attributes=False).encode()).hexdigest() == node_sha


def test_original_defensive_ledger_is_retained_without_discharging_properties():
    expected = {
        "web": "8e84695475388f58a28778863152f50cd947f5ee5b7fc595d85d56fbc84ff29f",
        "ops": "f0e92df3fe1e00aa6636c9654f17442fcfd4319c288fd78d1421262d818d7bc4",
        "ai": "77981ac6c6e9a0550c11973fe231dc8dc981d57dc60049446095a0fad465382b",
    }
    cases = []
    for cohort, digest in expected.items():
        payload = (ROOT / "benchmark" / "round8_security" / (cohort + "_challenges.json")).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == digest
        cases.extend(json.loads(payload)["challenges"])
    assert len(cases) == len({case["project_id"] for case in cases}) == 14
    assert sum(len(case["required_properties"]) for case in cases) == 60
    assert all(case["status"] == "unproved" and case["vulnerability_label"] is None for case in cases)


BOUNDARY_MODEL = '''events = []
result = None
completed_config = None
async def selected(*, identity):
    events.append("selected")
    return identity
class DeferredConfig:
    def __init__(self, config):
        self.config = config
    def __await__(self):
        if False:
            yield "not-the-completion"
        return self.config
async def modifier(config, request):
    global completed_config
    replacement = {"configurable": {"callback": selected, "identity": request["identity"]}}
    completed_config = __COMPLETION_EXPRESSION__
    return completed_config
async def unpack(projected_config, request, per_req_config_modifier):
    projected_config = await per_req_config_modifier(projected_config, request)
    return projected_config
async def endpoint():
    global result
    config = await unpack({}, {"identity": "request-17"}, modifier)
    nested = config["configurable"]
    call = nested["callback"]
    call_args = {"identity": nested["identity"]}
    result = await call(**call_args)
pending = endpoint()
'''
RESUME = '''try:
    pending.send(None)
except StopIteration:
    pass
'''


def boundary_source(*, custom=True, resume=True):
    expression = "await DeferredConfig(replacement)" if custom else "replacement"
    return BOUNDARY_MODEL.replace("__COMPLETION_EXPRESSION__", expression) + (RESUME if resume else "")


def _concrete(source):
    namespace = {}
    exec(compile(source, "<reviewed-framework-boundary>", "exec"), namespace)
    state = {"result": namespace["result"], "events": list(namespace["events"]),
             "completed_config": namespace["completed_config"]}
    namespace["pending"].close()  # host cleanup, not an added analyzed source operation
    return state


def _literal_values(data, name):
    entities = {entity["id"]: entity for entity in data["entities"]}
    return {entities[object_id]["attributes"]["value"] for object_id in values(data, name)
            if "value" in entities[object_id]["attributes"]}


@pytest.mark.parametrize("custom", [False, True])
def test_cpython_await_completion_returns_the_fresh_config_and_request_value(custom):
    state = _concrete(boundary_source(custom=custom))
    assert state["result"] == "request-17"
    assert state["events"] == ["selected"]
    assert state["completed_config"]["configurable"]["identity"] == "request-17"


def test_direct_native_coroutine_return_control_recovers_callback_value_and_effect(tmp_path):
    data = analyze(tmp_path, boundary_source(custom=False))
    assert ("endpoint", "selected") in edges(data)
    assert "request-17" in _literal_values(data, "result")
    assert "selected" in event_values(data)


def test_unresumed_boundary_model_does_not_publish_callback_effect(tmp_path):
    source = boundary_source(resume=False)
    assert _concrete(source)["events"] == []
    data = analyze(tmp_path, source)
    assert ("endpoint", "selected") not in edges(data)
    assert event_values(data) == set()


def test_custom_await_completion_recovers_framework_callback_value_and_effect(tmp_path):
    source = boundary_source()
    concrete = _concrete(source)
    assert concrete["result"] == "request-17" and concrete["events"] == ["selected"]
    data = analyze(tmp_path, source)
    missing = {
        "callback_call": ("endpoint", "selected") not in edges(data),
        "request_result_value": "request-17" not in _literal_values(data, "result"),
        "callback_heap_effect": "selected" not in event_values(data),
    }
    assert not any(missing.values()), missing
    # A custom await's result is the iterator completion, not a yielded element.
    assert "not-the-completion" not in _literal_values(data, "completed_config")


def test_custom_await_yielded_poison_is_not_a_completion_result(tmp_path):
    source = boundary_source()
    concrete = _concrete(source)
    assert isinstance(concrete["completed_config"], dict)
    data = analyze(tmp_path, source)
    assert "not-the-completion" not in _literal_values(data, "completed_config")


def test_yield_as_completion_mutation_is_killed_by_the_framework_oracle(tmp_path, monkeypatch):
    """Restore only the wrong value-channel choice, without editing native files."""
    from dataclasses import replace
    from pythonstan.analysis.pointer.kcfa.constraints import AwaitConstraint
    from pythonstan.analysis.pointer.kcfa.processor.generator import GeneratorProcessor
    original = GeneratorProcessor.handle_constraint

    def wrong_channel(self, solver, target, scope, constraint, points):
        if isinstance(constraint, AwaitConstraint) and constraint.operation == "await_complete":
            constraint = replace(constraint, operation="advance")
        return original(self, solver, target, scope, constraint, points)

    source = boundary_source()
    concrete = _concrete(source)
    assert concrete["result"] == "request-17" and concrete["events"] == ["selected"]
    monkeypatch.setattr(GeneratorProcessor, "handle_constraint", wrong_channel)
    mutated = analyze(tmp_path, source)
    assert ("endpoint", "selected") not in edges(mutated)
    assert "request-17" not in _literal_values(mutated, "result")
    assert "selected" not in event_values(mutated)
    assert "not-the-completion" in _literal_values(mutated, "completed_config")


def _snapshot_after(solver, path):
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle
    return load_bundle(write_bundle(capture(solver), path)).data["snapshot"]


def _global_locations(solver):
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    return {location.content.name: location for location in solver.state._env
            if isinstance(location, Ctx) and isinstance(location.content, Variable)
            and location.content.kind.value == "global"}


def test_existing_completion_facts_replay_into_a_late_await_consumer(tmp_path):
    """The first real next() suspends at yield; a later await returns its value."""
    prefix = BOUNDARY_MODEL.split("class DeferredConfig:")[0]
    suffix = BOUNDARY_MODEL.split("async def modifier", 1)[1]
    source = prefix + '''def completion(config):
    yield None
    return config
replacement = {"configurable": {"callback": selected, "identity": "request-17"}}
shared = completion(replacement)
next(shared)
class DeferredConfig:
    def __init__(self, config):
        pass
    def __await__(self):
        return shared
async def modifier''' + suffix.replace("__COMPLETION_EXPRESSION__", "await DeferredConfig(replacement)")
    source += '''def drive():
    try:
        pending.send(None)
    except StopIteration:
        pass
'''
    concrete = _concrete(source + "drive()\n")
    assert concrete["result"] == "request-17" and concrete["events"] == ["selected"]
    before, solver = analyze(tmp_path, source, retain=True)
    assert event_values(before) == set()
    # Establish the old-fact precondition independently of the eventual DTO.
    from pythonstan.analysis.pointer.kcfa.context import Ctx, CallSite
    from pythonstan.analysis.pointer.kcfa.variable import FieldAccess, Variable, VariableKind
    completion_slots = [points for location, points in solver.state._env.items()
        if isinstance(location, Ctx) and isinstance(location.content, FieldAccess)
        and location.content.field.kind.value == "class_metadata"
        and location.content.field.name == "generator.completion_value"]
    assert any(not points.is_empty() for points in completion_slots)
    locations = _global_locations(solver)
    scope = locations["pending"].scope
    from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
    from pythonstan.ir import IRCall
    statement = ast.parse("drive()").body[0].value
    ast.increment_lineno(statement, len(source.splitlines()))
    site = CallSite(IRCall(statement), scope_name=scope.stmt.get_qualname())
    # This is the explicit final source drive(), staged after an existing cut.
    solver.add_constraint(scope, scope.context,
        CallConstraint(Variable("drive", VariableKind.GLOBAL), (), frozenset(), None, site))
    solver.solve_to_fixpoint()
    after = _snapshot_after(solver, tmp_path / "after")
    assert ("endpoint", "selected") in edges(after)
    assert "request-17" in _literal_values(after, "result")
    assert "selected" in event_values(after)


def test_late_wrapper_iterator_fact_replays_completion_and_callback(tmp_path):
    """Controlled abstract input growth, not a claim about a failed Python run."""
    source = BOUNDARY_MODEL.replace("class DeferredConfig:\n", '''incoming = None
ready = None
def completion(config):
    if False:
        yield "not-the-completion"
    return config
class DeferredConfig:
''').replace("        return self.config\n", "        return incoming\n")
    # __await__ itself is now a normal method returning an iterator, so remove
    # its lexical yield while preserving the generator producer above.
    source = source.replace('''    def __await__(self):
        if False:
            yield "not-the-completion"
        return incoming
''', '''    def __await__(self):
        return incoming
''')
    source = source.replace("    global completed_config\n", "    global completed_config, ready, incoming\n")
    source = source.replace("    completed_config = __COMPLETION_EXPRESSION__\n",
        "    ready = completion(replacement)\n    completed_config = await DeferredConfig(replacement)\n")
    source += RESUME
    # An actual valid input variant is checked by CPython. The staged engine
    # fixture below only adds that iterator alternative to an existing holder.
    valid_input = source.replace("    ready = completion(replacement)\n",
        "    ready = completion(replacement)\n    incoming = ready\n")
    assert _concrete(valid_input)["events"] == ["selected"]
    before, solver = analyze(tmp_path, source, retain=True)
    assert event_values(before) == set()
    locations = _global_locations(solver)
    from pythonstan.analysis.pointer.kcfa.object import GeneratorObject
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    incoming = locations["incoming"]
    generators = PointsToSet.from_objects(obj for obj in solver.state._env[locations["ready"]]
                                         if isinstance(obj, GeneratorObject))
    assert not generators.is_empty()
    solver.handle_new_points_to(incoming, incoming.scope, generators)
    solver.solve_to_fixpoint()
    after = _snapshot_after(solver, tmp_path / "after")
    assert ("endpoint", "selected") in edges(after)
    assert "request-17" in _literal_values(after, "result")
    assert "selected" in event_values(after)
    assert "not-the-completion" not in _literal_values(after, "completed_config")


@pytest.mark.parametrize("choose_value", [False, True])
def test_conditional_explicit_value_and_none_completions_remain_distinct_from_yield(tmp_path, choose_value):
    source = '''events = []
completed_config = None
result = "not-resumed"
class Conditional:
    def __init__(self, choose):
        self.choose = choose
    def __await__(self):
        if self.choose:
            return "completion-value"
        else:
            return None
        yield "yielded-poison"
async def endpoint():
    global result
    result = await Conditional(CHOOSE)
pending = endpoint()
'''.replace("CHOOSE", repr(choose_value)) + RESUME
    expected = "completion-value" if choose_value else None
    assert _concrete(source)["result"] == expected
    data = analyze(tmp_path, source)
    represented = _literal_values(data, "result")
    assert expected in represented
    assert "yielded-poison" not in represented
