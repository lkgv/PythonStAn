"""Finite, nonexecuting guarded-dispatch-v1 candidate language.

There is no Python expression evaluator in this DSL. A proposal has no field
for selecting ignored source, guard evidence, verification status or tools.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping

from pythonstan.analysis.contracts.identity import SemanticIdentity, VersionedDependency
from pythonstan.analysis.contracts import freeze_snapshot, thaw_snapshot

PROFILE = "guarded-dispatch-v1"
SCHEMA_VERSION = 1
RELATIONS = frozenset({"input", "fresh_box", "top"})


class CandidateError(ValueError):
    pass


def _keys(value, names, label):
    if not isinstance(value, Mapping) or set(value) != set(names):
        raise CandidateError(label + " has missing or unknown fields")


def _string(value, label):
    if type(value) is not str or not value or len(value) > 4096:
        raise CandidateError(label + " must be a bounded nonempty string")
    return value


def _strings(value, label):
    if not isinstance(value, (list, tuple)) or len(value) > 4096:
        raise CandidateError(label + " must be a bounded list")
    values = tuple(_string(item, label) for item in value)
    if len(set(values)) != len(values):
        raise CandidateError(label + " contains duplicates")
    return values


def _key(value):
    if type(value) is not str or len(value) > 4096:
        raise CandidateError("key must be a bounded literal string")
    return value


@dataclass(frozen=True)
class FiniteGuard:
    table_name: str
    keys: tuple[str, ...]
    exact_builtin_dict: bool = True
    stable_bindings: bool = True
    payload: str = "all_modeled_values"


@dataclass(frozen=True)
class HandlerClaim:
    name: str
    relation: str
    input_parameter: str
    slot: str | None = None
    existing_heap: str = "unchanged"
    normal_exit: bool = True
    exceptions: tuple[str, ...] = ()
    suspends: bool = False


@dataclass(frozen=True)
class DispatchArm:
    key: str
    handler: str
    relation: str


@dataclass(frozen=True)
class GuardedSummary:
    identity: SemanticIdentity
    dispatch: str
    guard: FiniteGuard
    handlers: tuple[HandlerClaim, ...]
    arms: tuple[DispatchArm, ...]
    schema_version: int = SCHEMA_VERSION
    profile: str = PROFILE


def identity_to_dict(identity):
    return {"source_digest":identity.source_digest, "ir_digest":identity.ir_digest,
            "model_digest":identity.model_digest, "policy_digest":identity.policy_digest,
            "profile":identity.profile, "state_version":identity.state_version,
            "python_version":identity.python_version,
            "dependencies":[{"kind":d.kind,"identity":d.identity,"version":d.version,
                             "negative":d.negative} for d in identity.dependencies]}


def parse_identity(data):
    _keys(data, ("source_digest","ir_digest","model_digest","policy_digest","profile",
                 "state_version","python_version","dependencies"), "identity")
    if not isinstance(data["dependencies"], (list,tuple)) or len(data["dependencies"]) > 4096:
        raise CandidateError("identity dependencies must be bounded")
    dependencies = []
    for dependency in data["dependencies"]:
        _keys(dependency, ("kind","identity","version","negative"), "dependency")
        if type(dependency["negative"]) is not bool:
            raise CandidateError("negative dependency flag must be boolean")
        dependencies.append(VersionedDependency(_string(dependency["kind"],"dependency kind"),
            _string(dependency["identity"],"dependency identity"),
            _string(dependency["version"],"dependency version"),dependency["negative"]))
    fields = {name:_string(data[name],name) for name in
              ("source_digest","ir_digest","model_digest","policy_digest","profile",
               "state_version","python_version")}
    return SemanticIdentity(**fields, dependencies=tuple(dependencies))


def candidate_to_dict(candidate):
    return {"schema_version":candidate.schema_version,"profile":candidate.profile,
            "identity":identity_to_dict(candidate.identity),"dispatch":candidate.dispatch,
            "guard":{"table_name":candidate.guard.table_name,"keys":list(candidate.guard.keys),
                     "exact_builtin_dict":candidate.guard.exact_builtin_dict,
                     "stable_bindings":candidate.guard.stable_bindings,"payload":candidate.guard.payload},
            "handlers":[{"name":h.name,"relation":h.relation,"input_parameter":h.input_parameter,
                         "slot":h.slot,"existing_heap":h.existing_heap,"normal_exit":h.normal_exit,
                         "exceptions":list(h.exceptions),"suspends":h.suspends} for h in candidate.handlers],
            "arms":[{"key":a.key,"handler":a.handler,"relation":a.relation} for a in candidate.arms]}


def _no_duplicates(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise CandidateError("duplicate JSON key: " + key)
        result[key] = value
    return result


def parse_candidate(data):
    if isinstance(data, GuardedSummary):
        data = candidate_to_dict(data)
    if isinstance(data, str):
        if len(data.encode("utf-8")) > 1_000_000:
            raise CandidateError("candidate byte budget exceeded")
        data = json.loads(data, object_pairs_hook=_no_duplicates)
    elif isinstance(data,Mapping):
        # Direct Python/recorded providers obey the same byte/JSON boundary as
        # text providers; live objects and large mappings cannot bypass it.
        try:
            data = thaw_snapshot(freeze_snapshot(data))
            if len(json.dumps(data,ensure_ascii=False,allow_nan=False).encode()) > 1_000_000:
                raise CandidateError("candidate byte budget exceeded")
        except (TypeError,ValueError,RecursionError) as error:
            raise CandidateError(str(error)) from error
    _keys(data, ("schema_version","profile","identity","dispatch","guard","handlers","arms"), "candidate")
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION or data["profile"] != PROFILE:
        raise CandidateError("candidate schema/profile mismatch")
    guard = data["guard"]
    _keys(guard, ("table_name","keys","exact_builtin_dict","stable_bindings","payload"), "guard")
    if guard["exact_builtin_dict"] is not True or guard["stable_bindings"] is not True or guard["payload"] != "all_modeled_values":
        raise CandidateError("guard must require an exact stable builtin dict and symbolic payload")
    if not isinstance(guard["keys"],(list,tuple)) or len(guard["keys"]) > 4096:
        raise CandidateError("keys must be a bounded list")
    guard_keys = tuple(_key(key) for key in guard["keys"])
    if len(set(guard_keys)) != len(guard_keys):
        raise CandidateError("duplicate guard keys")
    parsed_guard = FiniteGuard(_string(guard["table_name"],"table name"),guard_keys)
    handlers = []
    if not isinstance(data["handlers"], (list,tuple)) or len(data["handlers"]) > 4096:
        raise CandidateError("handlers must be a bounded list")
    for handler in data["handlers"]:
        _keys(handler, ("name","relation","input_parameter","slot","existing_heap","normal_exit","exceptions","suspends"), "handler")
        relation = handler["relation"]
        if relation not in RELATIONS or handler["existing_heap"] != "unchanged":
            raise CandidateError("unknown relation or frame contract")
        if type(handler["normal_exit"]) is not bool or type(handler["suspends"]) is not bool:
            raise CandidateError("outcome flags must be booleans")
        slot = handler["slot"]
        if slot is not None and type(slot) is not str:
            raise CandidateError("slot must be a literal string or null")
        if relation == "fresh_box" and slot != "value":
            raise CandidateError("fresh_box has the single exact value slot")
        if relation in ("input","top") and slot is not None:
            raise CandidateError("input/top have no exact output slot")
        handlers.append(HandlerClaim(_string(handler["name"],"handler name"),relation,
            _string(handler["input_parameter"],"input parameter"),slot,"unchanged",
            handler["normal_exit"],_strings(handler["exceptions"],"exceptions"),handler["suspends"]))
    if len({h.name for h in handlers}) != len(handlers):
        raise CandidateError("duplicate handler claims")
    if not isinstance(data["arms"], (list,tuple)) or len(data["arms"]) > 4096:
        raise CandidateError("arms must be a bounded list")
    arms = []
    for arm in data["arms"]:
        _keys(arm,("key","handler","relation"),"arm")
        if arm["relation"] not in RELATIONS:
            raise CandidateError("unknown arm relation")
        arms.append(DispatchArm(_key(arm["key"]),_string(arm["handler"],"handler"),arm["relation"]))
    if len({a.key for a in arms}) != len(arms):
        raise CandidateError("duplicate key arms")
    identity = parse_identity(data["identity"])
    if identity.profile != PROFILE:
        raise CandidateError("identity profile mismatch")
    return GuardedSummary(identity,_string(data["dispatch"],"dispatch"),parsed_guard,
                          tuple(handlers),tuple(arms))
