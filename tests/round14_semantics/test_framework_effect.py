"""Source-bound mapping effects and a narrow, separately checked policy goal.

The LangServe-shaped consumer is a reviewed boundary model, not the package.
The Lightning oracle executes only the pinned _load function AST with inert
boundary/filesystem stubs; no target package, checkpoint, or network is used.
Neither experiment proves deployment reachability or native loading safety.
"""
import __future__
import ast
import copy
import hashlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pythonstan.analysis.security.boundary_policy import (
    REQUIRED_CONTRACTS,
    UnsupportedPolicyModel,
    check_boundary_policy,
    infer_boundary_policy,
)
from tests.round12_semantics.test_continuations import analyze, edges, event_values, values
from tests.round13_semantics.test_framework_frontier import _qualified_nodes
from tests.round13_semantics.test_framework_frontier import _global_locations, _snapshot_after


ROOT = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ.get("PYTHONSTAN_CORPUS_CACHE", ROOT.parent / "round8_corpus"))
LANGSERVE_PATH = "langserve/langserve-0.3.3/langserve/api_handler.py"
LIGHTNING_PATH = "lightning/lightning-2.6.6/src/lightning/fabric/utilities/cloud_io.py"
LANGSERVE_SHA = "1645456302c9710fe41d746af44cf0c78e8a1748c231701e37c931abb502b83b"
LIGHTNING_SHA = "890ffcb7827a18831163c2dbb4e5d123404969d5f3863d2ce6608c23c77898c3"

BINDINGS = (
    (LANGSERVE_PATH, LANGSERVE_SHA, "langserve.api_handler",
     "langserve.api_handler._unpack_request_config", 194,
     "model(**config)",
     "6f07c28311c87ed97e5d20090f0610f826134860c94877d156e288e8ca7a287f"),
    (LIGHTNING_PATH, LIGHTNING_SHA, "lightning.fabric.utilities.cloud_io",
     "lightning.fabric.utilities.cloud_io._load", 53,
     "torch.load(path_or_url, map_location=map_location, weights_only=weights_only)",
     "ddafb43d63c2a6c6ff5e7d53884d51c976f9a5aab75a3733a52dc523fb2e6194"),
    (LIGHTNING_PATH, LIGHTNING_SHA, "lightning.fabric.utilities.cloud_io",
     "lightning.fabric.utilities.cloud_io._load", 60,
     "weights_only = False",
     "87d95f740b720a23f10cbe06ab62a81b0332cacdca73a0a679d798c8dfc66112"),
    (LIGHTNING_PATH, LIGHTNING_SHA, "lightning.fabric.utilities.cloud_io",
     "lightning.fabric.utilities.cloud_io._load", 66,
     "torch.hub.load_state_dict_from_url(str(path_or_url), map_location=map_location, weights_only=weights_only)",
     "8892fbfd3271e1137d3034fc5a18711ff9dc824f9f00d657fd487f160394bb0e"),
    (LIGHTNING_PATH, LIGHTNING_SHA, "lightning.fabric.utilities.cloud_io",
     "lightning.fabric.utilities.cloud_io._load", 73,
     "torch.load(f, map_location=map_location, weights_only=weights_only)",
     "994990463ba9fb9f77ac2a58a8696dbb4f464f2710459bac6f1276f7c92e909d"),
)


def _source(relative, expected_sha):
    path = CACHE / relative
    if not path.exists():
        pytest.skip("rehydrate the exact frozen corpus for source-bound checks")
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected_sha
    return raw.decode("utf-8")


@pytest.mark.parametrize("relative,file_sha,module,qualname,line,expression,node_sha", BINDINGS)
def test_pinned_framework_effect_source_binding(relative, file_sha, module, qualname, line, expression, node_sha):
    tree = ast.parse(_source(relative, file_sha), filename=relative)
    matches = _qualified_nodes(tree, module).get((qualname, line, expression), [])
    assert len(matches) == 1
    assert hashlib.sha256(ast.dump(matches[0], include_attributes=False).encode()).hexdigest() == node_sha
    if relative == LANGSERVE_PATH:
        # This is specifically the already-selected Mapping arm, not the str or
        # Pydantic arms, and does not add a new application entry/root.
        branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                      and ast.unparse(n.test) == "isinstance(config, Mapping)")
        assert any(n is matches[0] for statement in branch.body for n in ast.walk(statement))


MAPPING_MODEL = '''from collections.abc import Mapping
events = []
result = None
def selected(identity):
    events.append(identity)
    return "accepted"
class ConfigMapping(Mapping):
    def keys(self):
        events.append("keys")
        return ["callback", "identity"]
    def __iter__(self):
        return iter(["callback", "identity"])
    def __len__(self):
        return 2
    def __getitem__(self, name):
        events.append("getitem")
        if name == "callback":
            return selected
        return "request-14"
__CONSUMER__
config = __CONFIG__
result = __CALL__
'''
CONSUMERS = {
    "named": '''def model(*, callback, identity):
    events.append("model")
    return callback(identity)''',
    "collector": '''def model(**arguments):
    events.append("model")
    return arguments["callback"](arguments["identity"])''',
}
# Frozen before the production run; ordering/multiplicity is a CPython oracle
# only. The flow-insensitive native oracle asks for these finite may-effects.
CUSTOM_GOLD = ("accepted", ("keys", "getitem", "getitem", "model", "request-14"))
DICT_GOLD = ("accepted", ("model", "request-14"))
EXPLICIT_GOLD = ("accepted", ("getitem", "getitem", "model", "request-14"))


def mapping_source(*, consumer="named", native_dict=False, explicit=False):
    config = '{"callback": selected, "identity": "request-14"}' if native_dict else "ConfigMapping()"
    call = 'model(callback=config["callback"], identity=config["identity"])' if explicit else "model(**config)"
    return (MAPPING_MODEL.replace("__CONSUMER__", CONSUMERS[consumer])
            .replace("__CONFIG__", config).replace("__CALL__", call))


def _concrete_mapping(source):
    namespace = {}
    exec(compile(source, "<reviewed-mapping-boundary>", "exec"), namespace)
    return namespace["result"], tuple(namespace["events"])


def _literal_values(data, name):
    entities = {entity["id"]: entity for entity in data["entities"]}
    return {entities[object_id]["attributes"]["value"] for object_id in values(data, name)
            if "value" in entities[object_id]["attributes"]}


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_cpython_custom_mapping_keyword_expansion_effect_and_value_gold(consumer):
    assert _concrete_mapping(mapping_source(consumer=consumer)) == CUSTOM_GOLD


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_native_dict_keyword_expansion_control(tmp_path, consumer):
    source = mapping_source(consumer=consumer, native_dict=True)
    assert _concrete_mapping(source) == DICT_GOLD
    data = analyze(tmp_path, source)
    assert ("model", "selected") in edges(data)
    assert "accepted" in _literal_values(data, "result")
    assert {"model", "request-14"} <= event_values(data)
    assert "keys" not in event_values(data)


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_explicit_custom_mapping_getitem_control(tmp_path, consumer):
    source = mapping_source(consumer=consumer, explicit=True)
    assert _concrete_mapping(source) == EXPLICIT_GOLD
    data = analyze(tmp_path, source)
    assert ("model", "selected") in edges(data)
    assert "accepted" in _literal_values(data, "result")
    assert {"getitem", "model", "request-14"} <= event_values(data)


@pytest.mark.parametrize("consumer", CONSUMERS)
@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("intrinsics", [False, True])
def test_custom_mapping_keyword_expansion_preserves_protocol_callback_value_and_heap_effect(tmp_path, consumer, policy, intrinsics):
    source = mapping_source(consumer=consumer)
    assert _concrete_mapping(source) == CUSTOM_GOLD
    data = analyze(tmp_path, source, policy=policy, intrinsics=intrinsics)
    missing = {
        "keys_heap_effect": "keys" not in event_values(data),
        "getitem_heap_effect": "getitem" not in event_values(data),
        "callback_call": ("model", "selected") not in edges(data),
        "result_value": "accepted" not in _literal_values(data, "result"),
        "callback_heap_effect": "request-14" not in event_values(data),
    }
    assert not any(missing.values()), missing


def test_keyword_mapping_keys_uses_ordinary_instance_attribute_shadow(tmp_path):
    source = mapping_source().replace("result = model(**config)", '''def replacement_keys():
    events.append("shadow-keys")
    return ["callback", "identity"]
config.keys = replacement_keys
result = model(**config)''')
    assert _concrete_mapping(source) == (
        "accepted", ("shadow-keys", "getitem", "getitem", "model", "request-14"))
    data = analyze(tmp_path, source)
    assert "shadow-keys" in event_values(data)
    assert ("model", "selected") in edges(data)


def test_keyword_mapping_getitem_bypasses_instance_shadow(tmp_path):
    source = mapping_source().replace("result = model(**config)", '''def shadow_getitem(name):
    events.append("getitem-shadow-poison")
    return None
config.__getitem__ = shadow_getitem
result = model(**config)''')
    assert _concrete_mapping(source) == CUSTOM_GOLD
    data = analyze(tmp_path, source)
    assert {"keys", "getitem", "request-14"} <= event_values(data)
    assert "getitem-shadow-poison" not in event_values(data)
    assert not any(callee == "shadow_getitem" for _, callee in edges(data))


def test_keyword_mapping_generator_keys_uses_yields_not_completion(tmp_path):
    source = mapping_source().replace('''        return ["callback", "identity"]
    def __iter__''', '''        yield "callback"
        yield "identity"
        return "completion-poison"
    def __iter__''')
    assert _concrete_mapping(source) == CUSTOM_GOLD
    data = analyze(tmp_path, source)
    assert "accepted" in _literal_values(data, "result")
    assert "request-14" in event_values(data)
    assert "completion-poison" not in event_values(data)


def test_keyword_mapping_exact_nonstring_key_keeps_getitem_effect_without_named_argument(tmp_path):
    source = mapping_source().replace('return ["callback", "identity"]', 'return [13]')
    concrete = {}
    with pytest.raises(TypeError, match="keywords must be strings"):
        exec(compile(source, "<reviewed-invalid-key-boundary>", "exec"), concrete)
    assert concrete["events"] == ["keys", "getitem"]
    data = analyze(tmp_path, source)
    assert {"keys", "getitem"} <= event_values(data)
    assert ("model", "selected") not in edges(data)
    assert "accepted" not in _literal_values(data, "result")
    # The current source-call may-model can retain an invalid callee body;
    # argument validity / precise exception routing are not claimed proved.


def test_keyword_mapping_late_key_elements_replay_existing_getitem_and_consumer(tmp_path):
    source = mapping_source().replace("events = []", '''events = []
returned_keys = []
callback_key = "callback"
identity_key = "identity"''').replace('return ["callback", "identity"]', 'return returned_keys')
    valid = source.replace("returned_keys = []", 'returned_keys = ["callback", "identity"]')
    assert _concrete_mapping(valid) == CUSTOM_GOLD
    before, solver = analyze(tmp_path, source, retain=True)
    assert ("model", "selected") not in edges(before)
    locations = _global_locations(solver)
    from pythonstan.analysis.pointer.kcfa.heap_model import elem
    from pythonstan.analysis.pointer.kcfa.object import ListObject
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    lists = [obj for obj in solver.state._env[locations["returned_keys"]] if isinstance(obj, ListObject)]
    assert len(lists) == 1
    key_objects = PointsToSet.from_objects(
        obj for name in ("callback_key", "identity_key") for obj in solver.state._env[locations[name]])
    destination = solver.state.get_field(locations["returned_keys"].scope,
        locations["returned_keys"].context, lists[0], elem())
    # Controlled abstract input growth; this is not execution of the invalid
    # empty-mapping source. The valid concrete input above fixes the gold.
    solver.handle_new_points_to(destination, destination.scope, key_objects)
    solver.solve_to_fixpoint()
    after = _snapshot_after(solver, tmp_path / "late-keys")
    assert ("model", "selected") in edges(after)
    assert "accepted" in _literal_values(after, "result")
    assert "request-14" in event_values(after)


def test_keyword_mapping_late_getitem_result_reaches_existing_formal(tmp_path):
    source = mapping_source().replace("events = []", "events = []\nincoming = None")
    source = source.replace('return selected\n        return "request-14"',
                            'return incoming\n        return "request-14"')
    valid = source.replace("config = ConfigMapping()", "incoming = selected\nconfig = ConfigMapping()")
    assert _concrete_mapping(valid) == CUSTOM_GOLD
    before, solver = analyze(tmp_path, source, retain=True)
    assert ("model", "selected") not in edges(before)
    locations = _global_locations(solver)
    from pythonstan.analysis.pointer.kcfa.object import FunctionObject
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    functions = PointsToSet.from_objects(obj for obj in solver.state._env[locations["selected"]]
                                         if isinstance(obj, FunctionObject))
    assert not functions.is_empty()
    solver.handle_new_points_to(locations["incoming"], locations["incoming"].scope, functions)
    solver.solve_to_fixpoint()
    after = _snapshot_after(solver, tmp_path / "late-value")
    assert ("model", "selected") in edges(after)
    assert "accepted" in _literal_values(after, "result")
    assert "request-14" in event_values(after)


def test_keyword_mapping_late_type_getitem_method_replays_existing_keys(tmp_path):
    source = mapping_source().replace("    def __getitem__(self, name):", "def lookup(self, name):")
    # This structural protocol fixture admits a method added after class
    # creation, without ABCMeta's separate abstract-method refresh protocol.
    source = source.replace("class ConfigMapping(Mapping):", "class ConfigMapping(object):")
    source = source.replace('''        events.append("getitem")
        if name == "callback":
            return selected
        return "request-14"''', '''    events.append("getitem")
    if name == "callback":
        return selected
    return "request-14"''')
    valid = source.replace("config = ConfigMapping()", "ConfigMapping.__getitem__ = lookup\nconfig = ConfigMapping()")
    assert _concrete_mapping(valid) == CUSTOM_GOLD
    before, solver = analyze(tmp_path, source, retain=True)
    assert ("model", "selected") not in edges(before)
    locations = _global_locations(solver)
    from pythonstan.analysis.pointer.kcfa.heap_model import attr
    from pythonstan.analysis.pointer.kcfa.object import ClassObject, FunctionObject
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    classes = [obj for obj in solver.state._env[locations["ConfigMapping"]] if isinstance(obj, ClassObject)]
    assert len(classes) == 1
    functions = PointsToSet.from_objects(obj for obj in solver.state._env[locations["lookup"]]
                                         if isinstance(obj, FunctionObject))
    destination = solver.state.get_field(locations["ConfigMapping"].scope,
        locations["ConfigMapping"].context, classes[0], attr("__getitem__"))
    solver.handle_new_points_to(destination, destination.scope, functions)
    solver.solve_to_fixpoint()
    after = _snapshot_after(solver, tmp_path / "late-method")
    assert ("model", "selected") in edges(after)
    assert "accepted" in _literal_values(after, "result")
    assert "request-14" in event_values(after)


def _policy_request():
    return {
        "entry": "lightning_source._load", "policy": "weights_only", "forwarders": {},
        "boundaries": ["torch.load", "torch.hub.load_state_dict_from_url"],
        "boundary_imports": {"torch": "torch"},
        "entry_values": [False, True, None, "omitted"],
        "contracts": sorted(REQUIRED_CONTRACTS),
    }


def _policy_key(value):
    return "none" if value is None else "true" if value is True else "false"


def _source_kernel_observations(source):
    """CPython executes the original body with fixed, inert boundary bindings.

    The host stubs implement the explicit contracts. They do not infer policy
    values, fork abstract guards, call Torch, read a file, or fetch a URL.
    """
    tree = ast.parse(source, filename=LIGHTNING_PATH)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_load")
    assert hashlib.sha256(ast.dump(function, include_attributes=False).encode()).hexdigest() == (
        "a822fb8771efa07fad1d3fb2555857107f4b94bf3551d7a9f48d56ebb58a5139")
    observations = set()
    for supplied in (False, True, None, "omitted"):
        input_key = "omitted" if supplied == "omitted" else _policy_key(supplied)
        for source_kind in ("filelike", "url", "path"):
            calls = []

            def load_stub(boundary):
                def observe(*args, **kwargs):
                    assert len(args) == 1 and set(kwargs) == {"map_location", "weights_only"}
                    calls.append((input_key, "lightning_source._load", sys._getframe(1).f_lineno,
                                  boundary, _policy_key(kwargs["weights_only"])))
                    return "inert-boundary-result"
                return observe

            class Handle:
                def __enter__(self):
                    return object()

                def __exit__(self, *exc):
                    return False

            class Filesystem:
                def open(self, path, mode):
                    assert path == "/virtual/checkpoint" and mode == "rb"
                    return Handle()

            namespace = {
                "Path": Path,
                "torch": SimpleNamespace(load=load_stub("torch.load"), hub=SimpleNamespace(
                    load_state_dict_from_url=load_stub("torch.hub.load_state_dict_from_url"))),
                "get_filesystem": lambda path: Filesystem(),
                "log": SimpleNamespace(debug=lambda message: None),
            }
            module = ast.Module(body=[copy.deepcopy(function)], type_ignores=[])
            exec(compile(module, LIGHTNING_PATH, "exec", flags=__future__.annotations.compiler_flag,
                         dont_inherit=True), namespace)
            argument = {"filelike": object(), "url": "https://example.invalid/checkpoint",
                        "path": "/virtual/checkpoint"}[source_kind]
            kwargs = {} if supplied == "omitted" else {"weights_only": supplied}
            assert namespace["_load"](argument, **kwargs) == "inert-boundary-result"
            assert len(calls) == 1
            observations.update(calls)
    return observations


def _rows(candidate):
    return {(r["input"], r["function"], r["line"], r["boundary"], r["value"])
            for r in candidate["observations"]}


def test_lightning_original_body_narrow_policy_relation_has_cpython_and_finite_checker_agreement():
    source = _source(LIGHTNING_PATH, LIGHTNING_SHA)
    sources, request = {"lightning_source": source}, _policy_request()
    candidate = infer_boundary_policy(sources, request)
    concrete = _source_kernel_observations(source)
    assert len(concrete) == 12
    assert _rows(candidate) == concrete
    checked = check_boundary_policy(sources, request, candidate)
    assert checked["status"] == "conditional_model_verified"
    assert checked["boundary_cases_checked"] == 12 and checked["candidate_extra_cases"] == 0
    assert checked["whole_program_soundness_proved"] is False
    assert checked["native_safety_proved"] is False
    assert checked["end_to_end_challenges_discharged"] == 0
    # Narrow policy property, separate from trust/serialization/native effects:
    # an explicit True reaches all three boundary callsites as True.
    assert {row[-1] for row in concrete if row[0] == "true"} == {"true"}
    assert ("none", "lightning_source._load", 66,
            "torch.hub.load_state_dict_from_url", "false") in concrete


def test_lightning_policy_checker_kills_an_omitted_true_url_case():
    sources = {"lightning_source": _source(LIGHTNING_PATH, LIGHTNING_SHA)}
    request = _policy_request()
    candidate = infer_boundary_policy(sources, request)
    candidate["observations"] = [r for r in candidate["observations"]
                                 if not (r["input"] == "true" and r["line"] == 66)]
    result = check_boundary_policy(sources, request, candidate)
    assert result["status"] == "rejected" and result["missing"]


@pytest.mark.parametrize("mutation", ["source", "contracts"])
def test_lightning_policy_certificate_is_bound_to_exact_source_and_contracts(mutation):
    sources = {"lightning_source": _source(LIGHTNING_PATH, LIGHTNING_SHA)}
    request = _policy_request()
    candidate = infer_boundary_policy(sources, request)
    if mutation == "source":
        sources["lightning_source"] += "\n# source identity changed\n"
    else:
        request["contracts"] = request["contracts"][:-1]
    result = check_boundary_policy(sources, request, candidate)
    assert result["status"] in {"rejected", "unsupported"}


def test_finite_scalar_checker_does_not_claim_custom_mapping_effects():
    sources = {"lightning_source": '''import torch
def _load(config, weights_only=None):
    return torch.load(**config)
'''}
    request = _policy_request()
    with pytest.raises(UnsupportedPolicyModel, match="Expanded call arguments"):
        infer_boundary_policy(sources, request)
    checked = check_boundary_policy(sources, request, {})
    assert checked["status"] == "unsupported"
    assert checked["whole_program_soundness_proved"] is False
    assert checked["native_safety_proved"] is False


def test_flat_keyword_partition_accepts_missing_native_callsite_index():
    from pythonstan.analysis.pointer.kcfa.context import CallSite, CallStringContext
    from pythonstan.analysis.pointer.kcfa.protocol_context import KeywordPartition, ProtocolKeyContext
    from pythonstan.ir.ir_statements import IRCall
    statement = IRCall(ast.parse("consume()").body[0].value)
    operation = CallSite(statement, "subject", index=None)
    partition = KeywordPartition(operation, None, "summary", None)
    context = ProtocolKeyContext(CallStringContext((), 0), partition)
    assert context.partition.operation is operation
    assert context.partition.operation.index is None
    assert context.partition.key_kind == "summary"
