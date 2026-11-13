"""Finite, nonexecuting guarded-dispatch-v1 candidate language.

There is no Python expression evaluator in this DSL. A proposal has no field
for selecting ignored source, guard evidence, verification status or tools.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeGuard

from pythonstan.analysis.contracts import freeze_snapshot, thaw_snapshot
from pythonstan.analysis.contracts.cost_model import visit_json_text
from pythonstan.analysis.contracts.identity import SemanticIdentity, VersionedDependency
from pythonstan.analysis.contracts.json_data import JsonObject

PROFILE = "guarded-dispatch-v1"
SCHEMA_VERSION = 1
RELATIONS = frozenset({"input", "fresh_box", "top"})


class CandidateError(ValueError):
    pass


def _mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    if not isinstance(value, Mapping):
        return False
    raw: Mapping[object, object] = value
    return all(isinstance(key, str) for key in raw)


def _keys(value: object, names: Sequence[str], label: str) -> Mapping[str, object]:
    if not _mapping(value) or set(value) != set(names):
        raise CandidateError(label + " has missing or unknown fields")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)) or len(value) > 4096:
        raise CandidateError(label + " must be a bounded list")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or type(value) is not str or not value or len(value) > 4096:
        raise CandidateError(label + " must be a bounded nonempty string")
    return value


def _strings(value: object, label: str) -> tuple[str, ...]:
    values = tuple(_string(item, label) for item in _sequence(value, label))
    if len(set(values)) != len(values):
        raise CandidateError(label + " contains duplicates")
    return values


def _key(value: object) -> str:
    if not isinstance(value, str) or type(value) is not str or len(value) > 4096:
        raise CandidateError("key must be a bounded literal string")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise CandidateError(label + " must be boolean")
    return value


def _relation(value: object) -> str:
    if not isinstance(value, str) or value not in RELATIONS:
        raise CandidateError("unknown relation or frame contract")
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


def _admit_record(keys: tuple[str, ...], visit: Callable[[int], None]) -> None:
    visit(1)
    for key in keys:
        visit_json_text(key, visit)


def _admit_atom(value: str | int | bool | None, visit: Callable[[int], None]) -> None:
    if isinstance(value, str):
        visit_json_text(value, visit)
    else:
        visit(1)


def _admit_identity(identity: SemanticIdentity, visit: Callable[[int], None]) -> None:
    _admit_record(
        (
            "source_digest",
            "ir_digest",
            "model_digest",
            "policy_digest",
            "profile",
            "state_version",
            "python_version",
            "dependencies",
        ),
        visit,
    )
    for value in (
        identity.source_digest,
        identity.ir_digest,
        identity.model_digest,
        identity.policy_digest,
        identity.profile,
        identity.state_version,
        identity.python_version,
    ):
        _admit_atom(value, visit)
    visit(1)
    for dependency in identity.dependencies:
        _admit_record(("kind", "identity", "version", "negative"), visit)
        for dependency_value in (
            dependency.kind,
            dependency.identity,
            dependency.version,
            dependency.negative,
        ):
            _admit_atom(dependency_value, visit)


def identity_to_dict(
    identity: SemanticIdentity, *, visit: Callable[[int], None] | None = None
) -> JsonObject:
    if visit is not None:
        _admit_identity(identity, visit)
    return {
        "source_digest": identity.source_digest,
        "ir_digest": identity.ir_digest,
        "model_digest": identity.model_digest,
        "policy_digest": identity.policy_digest,
        "profile": identity.profile,
        "state_version": identity.state_version,
        "python_version": identity.python_version,
        "dependencies": [
            {"kind": d.kind, "identity": d.identity, "version": d.version, "negative": d.negative}
            for d in identity.dependencies
        ],
    }


def parse_identity(value: object) -> SemanticIdentity:
    data = _keys(
        value,
        (
            "source_digest",
            "ir_digest",
            "model_digest",
            "policy_digest",
            "profile",
            "state_version",
            "python_version",
            "dependencies",
        ),
        "identity",
    )
    dependencies: list[VersionedDependency] = []
    for item in _sequence(data["dependencies"], "identity dependencies"):
        dependency = _keys(item, ("kind", "identity", "version", "negative"), "dependency")
        dependencies.append(
            VersionedDependency(
                _string(dependency["kind"], "dependency kind"),
                _string(dependency["identity"], "dependency identity"),
                _string(dependency["version"], "dependency version"),
                _boolean(dependency["negative"], "negative dependency flag"),
            )
        )
    return SemanticIdentity(
        source_digest=_string(data["source_digest"], "source_digest"),
        ir_digest=_string(data["ir_digest"], "ir_digest"),
        model_digest=_string(data["model_digest"], "model_digest"),
        policy_digest=_string(data["policy_digest"], "policy_digest"),
        profile=_string(data["profile"], "profile"),
        state_version=_string(data["state_version"], "state_version"),
        python_version=_string(data["python_version"], "python_version"),
        dependencies=tuple(dependencies),
    )


def candidate_to_dict(
    candidate: GuardedSummary, *, visit: Callable[[int], None] | None = None
) -> JsonObject:
    if visit is not None:
        _admit_record(
            ("schema_version", "profile", "identity", "dispatch", "guard", "handlers", "arms"),
            visit,
        )
        _admit_atom(candidate.schema_version, visit)
        _admit_atom(candidate.profile, visit)
        _admit_identity(candidate.identity, visit)
        _admit_atom(candidate.dispatch, visit)
        _admit_record(
            ("table_name", "keys", "exact_builtin_dict", "stable_bindings", "payload"), visit
        )
        _admit_atom(candidate.guard.table_name, visit)
        visit(1)
        for key in candidate.guard.keys:
            _admit_atom(key, visit)
        _admit_atom(candidate.guard.exact_builtin_dict, visit)
        _admit_atom(candidate.guard.stable_bindings, visit)
        _admit_atom(candidate.guard.payload, visit)
        visit(1)
        for handler in candidate.handlers:
            _admit_record(
                (
                    "name",
                    "relation",
                    "input_parameter",
                    "slot",
                    "existing_heap",
                    "normal_exit",
                    "exceptions",
                    "suspends",
                ),
                visit,
            )
            for value in (
                handler.name,
                handler.relation,
                handler.input_parameter,
                handler.slot,
                handler.existing_heap,
                handler.normal_exit,
                handler.suspends,
            ):
                _admit_atom(value, visit)
            visit(1)
            for exception in handler.exceptions:
                _admit_atom(exception, visit)
        visit(1)
        for arm in candidate.arms:
            _admit_record(("key", "handler", "relation"), visit)
            for value in (arm.key, arm.handler, arm.relation):
                _admit_atom(value, visit)
    return {
        "schema_version": candidate.schema_version,
        "profile": candidate.profile,
        "identity": identity_to_dict(candidate.identity),
        "dispatch": candidate.dispatch,
        "guard": {
            "table_name": candidate.guard.table_name,
            "keys": list(candidate.guard.keys),
            "exact_builtin_dict": candidate.guard.exact_builtin_dict,
            "stable_bindings": candidate.guard.stable_bindings,
            "payload": candidate.guard.payload,
        },
        "handlers": [
            {
                "name": h.name,
                "relation": h.relation,
                "input_parameter": h.input_parameter,
                "slot": h.slot,
                "existing_heap": h.existing_heap,
                "normal_exit": h.normal_exit,
                "exceptions": list(h.exceptions),
                "suspends": h.suspends,
            }
            for h in candidate.handlers
        ],
        "arms": [
            {"key": a.key, "handler": a.handler, "relation": a.relation} for a in candidate.arms
        ],
    }


def _no_duplicates(
    pairs: list[tuple[str, object]], visit: Callable[[int], None] | None = None
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if visit is not None:
            visit(1)
        if key in result:
            raise CandidateError("duplicate JSON key: " + key)
        result[key] = value
    return result


def parse_candidate(value: object, *, visit: Callable[[int], None] | None = None) -> GuardedSummary:
    if visit is not None:
        visit(1)
    data: object = (
        candidate_to_dict(value, visit=visit) if isinstance(value, GuardedSummary) else value
    )
    if isinstance(data, str):
        if visit is not None:
            # Admit the atomic JSON decoder's bounded input before it can
            # allocate its result, including malformed and duplicate-key data.
            visit(len(data))
        if len(data.encode("utf-8")) > 1_000_000:
            raise CandidateError("candidate byte budget exceeded")

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            return _no_duplicates(pairs, visit)

        decoded: object = json.loads(data, object_pairs_hook=unique)
        data = decoded
    elif isinstance(data, Mapping):
        try:
            data = thaw_snapshot(freeze_snapshot(data, visit=visit), visit=visit)
            if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode()) > 1_000_000:
                raise CandidateError("candidate byte budget exceeded")
        except (TypeError, ValueError, RecursionError) as error:
            raise CandidateError(str(error)) from error
    candidate = _keys(
        data,
        ("schema_version", "profile", "identity", "dispatch", "guard", "handlers", "arms"),
        "candidate",
    )
    if (
        type(candidate["schema_version"]) is not int
        or candidate["schema_version"] != SCHEMA_VERSION
        or candidate["profile"] != PROFILE
    ):
        raise CandidateError("candidate schema/profile mismatch")
    guard = _keys(
        candidate["guard"],
        ("table_name", "keys", "exact_builtin_dict", "stable_bindings", "payload"),
        "guard",
    )
    if (
        guard["exact_builtin_dict"] is not True
        or guard["stable_bindings"] is not True
        or guard["payload"] != "all_modeled_values"
    ):
        raise CandidateError("guard must require an exact stable builtin dict and symbolic payload")
    guard_keys = tuple(_key(key) for key in _sequence(guard["keys"], "keys"))
    if len(set(guard_keys)) != len(guard_keys):
        raise CandidateError("duplicate guard keys")
    parsed_guard = FiniteGuard(_string(guard["table_name"], "table name"), guard_keys)
    handlers: list[HandlerClaim] = []
    for item in _sequence(candidate["handlers"], "handlers"):
        handler = _keys(
            item,
            (
                "name",
                "relation",
                "input_parameter",
                "slot",
                "existing_heap",
                "normal_exit",
                "exceptions",
                "suspends",
            ),
            "handler",
        )
        relation = _relation(handler["relation"])
        if handler["existing_heap"] != "unchanged":
            raise CandidateError("unknown relation or frame contract")
        slot = handler["slot"]
        if slot is not None and (not isinstance(slot, str) or type(slot) is not str):
            raise CandidateError("slot must be a literal string or null")
        if relation == "fresh_box" and slot != "value":
            raise CandidateError("fresh_box has the single exact value slot")
        if relation in ("input", "top") and slot is not None:
            raise CandidateError("input/top have no exact output slot")
        handlers.append(
            HandlerClaim(
                _string(handler["name"], "handler name"),
                relation,
                _string(handler["input_parameter"], "input parameter"),
                slot,
                "unchanged",
                _boolean(handler["normal_exit"], "normal exit flag"),
                _strings(handler["exceptions"], "exceptions"),
                _boolean(handler["suspends"], "suspension flag"),
            )
        )
    if len({handler.name for handler in handlers}) != len(handlers):
        raise CandidateError("duplicate handler claims")
    arms: list[DispatchArm] = []
    for item in _sequence(candidate["arms"], "arms"):
        arm = _keys(item, ("key", "handler", "relation"), "arm")
        arms.append(
            DispatchArm(
                _key(arm["key"]), _string(arm["handler"], "handler"), _relation(arm["relation"])
            )
        )
    if len({arm.key for arm in arms}) != len(arms):
        raise CandidateError("duplicate key arms")
    identity = parse_identity(candidate["identity"])
    if identity.profile != PROFILE:
        raise CandidateError("identity profile mismatch")
    return GuardedSummary(
        identity,
        _string(candidate["dispatch"], "dispatch"),
        parsed_guard,
        tuple(handlers),
        tuple(arms),
    )


__all__ = [
    "PROFILE",
    "CandidateError",
    "DispatchArm",
    "FiniteGuard",
    "GuardedSummary",
    "HandlerClaim",
    "candidate_to_dict",
    "identity_to_dict",
    "parse_candidate",
    "parse_identity",
]
