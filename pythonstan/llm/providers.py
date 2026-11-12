"""Deterministic offline L2 proposers and an explicitly enabled factory.

Proposers deliberately do not validate source semantics. The trusted checker
rebuilds the complete source obligations independently after every proposal.
"""

from __future__ import annotations

import ast
import hashlib
import time
from collections.abc import Mapping
from copy import deepcopy
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts import SemanticIdentity, SourceModule, VersionedDependency
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_array,
    require_bool,
    require_int,
    require_object,
    require_string,
    validate_json_object,
)
from pythonstan.config import EffectiveConfig

from .candidates import CandidateProposal, CandidateProvider, SourceBundle

if TYPE_CHECKING:
    from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider


def _ast_digest(node: ast.AST) -> str:
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def _literal_table(node: ast.Assign) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Recognize the proposer's finite string-table syntax without trusting it."""
    if not isinstance(node.value, ast.Dict) or not node.targets:
        raise ValueError("No finite dispatch table")
    target = node.targets[0]
    if not isinstance(target, ast.Name):
        raise ValueError("Dispatch table needs a named binding")
    table: list[tuple[str, str]] = []
    for key, value in zip(node.value.keys, node.value.values, strict=True):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise ValueError("Dispatch keys must be literal strings")
        if not isinstance(value, ast.Name):
            raise ValueError("Dispatch handlers must be named functions")
        table.append((key.value, value.id))
    return target.id, tuple(table)


def _adversarial_change(wire: JsonObject, mutation: str | None) -> None:
    arms = require_array(wire["arms"])
    handlers = require_array(wire["handlers"])
    if mutation == "wrong_handler":
        first = require_object(arms[0])
        alternatives = [
            require_string(require_object(h)["name"])
            for h in handlers
            if require_object(h)["name"] != first["handler"]
        ]
        first["handler"] = alternatives[0] if alternatives else "invented_handler"
    elif mutation == "missing_handler":
        wire["handlers"] = handlers[:-1]
    elif mutation == "wrong_fresh":
        for item in arms:
            arm = require_object(item)
            if arm["relation"] == "fresh_box":
                arm["relation"] = "input"
                break
    elif mutation == "stale_ir":
        require_object(wire["identity"])["ir_digest"] = "stale"
    elif mutation == "self_verified":
        wire["verified"] = True


class OfflineDispatchProposer:
    def __init__(
        self, *, coarse: bool = False, adversarial: str | None = None, max_work: int = 100000
    ) -> None:
        self.coarse: bool = coarse
        self.adversarial: str | None = adversarial
        self.max_work: int = max_work

    def propose(
        self, source: str, *, identity: SemanticIdentity | None = None, max_work: int | None = None
    ) -> CandidateProposal:
        from pythonstan.analysis.verification.guarded_dispatch.language import (
            PROFILE,
            DispatchArm,
            FiniteGuard,
            GuardedSummary,
            HandlerClaim,
            candidate_to_dict,
        )
        from pythonstan.analysis.verification.guarded_dispatch.obligations import MODEL_DIGEST

        started = time.monotonic()
        work = 0
        try:
            if type(source) is not str or len(source.encode()) > 1_000_000:
                return CandidateProposal(
                    "budget_exhausted",
                    None,
                    "offline",
                    0,
                    time.monotonic() - started,
                    ("source budget",),
                )
            tree = ast.parse(source, type_comments=True, feature_version=(3, 12))
            work = sum(1 for _ in ast.walk(tree))
            limit = self.max_work if max_work is None else min(self.max_work, max(0, max_work))
            if work > limit:
                return CandidateProposal(
                    "budget_exhausted",
                    None,
                    "offline",
                    limit,
                    time.monotonic() - started,
                    ("proposal work budget",),
                )
            functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
            dispatch = next(node for node in functions if len(node.args.args) == 3)
            table_node = next(
                node
                for node in tree.body
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
            )
            table_name, table = _literal_table(table_node)
            handlers = []
            for function in functions:
                if function is dispatch:
                    continue
                returns = [node for node in ast.walk(function) if isinstance(node, ast.Return)]
                relation = (
                    "fresh_box" if returns and isinstance(returns[-1].value, ast.Dict) else "input"
                )
                if self.coarse:
                    relation = "top"
                handlers.append(
                    HandlerClaim(
                        function.name,
                        relation,
                        function.args.args[0].arg,
                        "value" if relation == "fresh_box" else None,
                    )
                )
            claims = {handler.name: handler for handler in handlers}
            if identity is None:
                dependencies = tuple(
                    sorted(
                        [
                            VersionedDependency("function", function.name, _ast_digest(function))
                            for function in functions
                        ]
                        + [
                            VersionedDependency("table", table_name, _ast_digest(table_node)),
                            VersionedDependency("model", PROFILE, MODEL_DIGEST),
                        ],
                        key=lambda item: (item.kind, item.identity),
                    )
                )
                identity = SemanticIdentity(
                    hashlib.sha256(source.encode()).hexdigest(),
                    _ast_digest(tree),
                    MODEL_DIGEST,
                    "default-policy",
                    PROFILE,
                    dependencies=dependencies,
                )
            candidate = GuardedSummary(
                identity,
                dispatch.name,
                FiniteGuard(table_name, tuple(key for key, _ in table)),
                tuple(handlers),
                tuple(
                    DispatchArm(key, handler, claims[handler].relation) for key, handler in table
                ),
            )
            wire = candidate_to_dict(candidate)
            _adversarial_change(wire, self.adversarial)
            return CandidateProposal(
                "candidate",
                wire,
                "adversarial" if self.adversarial else "offline",
                work,
                time.monotonic() - started,
            )
        except (
            SyntaxError,
            ValueError,
            AttributeError,
            IndexError,
            KeyError,
            StopIteration,
            RecursionError,
        ) as error:
            return CandidateProposal(
                "unavailable", None, "offline", work, time.monotonic() - started, (str(error),)
            )


class RecordedDispatchProposer:
    def __init__(self, recordings: Mapping[str, object]) -> None:
        self.recordings: dict[str, object] = deepcopy(dict(recordings))

    def propose(
        self, source: str, *, identity: SemanticIdentity | None = None, max_work: int | None = None
    ) -> CandidateProposal:
        started = time.monotonic()
        if max_work is not None and max_work < 1:
            return CandidateProposal(
                "budget_exhausted",
                None,
                "recorded",
                0,
                time.monotonic() - started,
                ("proposal work budget",),
            )
        digest = hashlib.sha256(source.encode()).hexdigest()
        candidate = self.recordings.get(digest)
        return CandidateProposal(
            "candidate" if candidate is not None else "unavailable",
            deepcopy(candidate),
            "recorded",
            1,
            time.monotonic() - started,
        )


def make_summary_provider(
    snapshot: SourceBundle | Mapping[str, SourceModule],
    config: EffectiveConfig | Mapping[str, object],
    remaining_work: int | None = None,
) -> GuardedDispatchProvider | None:
    """Assemble only explicitly enabled L2; proposals retain no trust authority."""
    options = (
        config.to_dict() if isinstance(config, EffectiveConfig) else validate_json_object(config)
    )
    llm = require_object(options.get("llm", {}))
    if not require_bool(llm.get("enabled", False)) or not require_bool(llm.get("level2", False)):
        return None
    provider_name = require_string(llm.get("provider", "offline"))
    proposer: CandidateProvider
    if provider_name == "recorded":
        recordings: dict[str, object] = {}
        for record in require_array(llm.get("proposals", [])):
            if isinstance(record, dict) and record.get("level") == 2:
                digest = record.get("source_digest")
                if isinstance(digest, str):
                    recordings[digest] = record.get("candidate")
        proposer = RecordedDispatchProposer(recordings)
    else:
        limit = require_int(require_object(options.get("resources", {})).get("max_work", 100000))
        if remaining_work is not None:
            limit = min(limit, max(0, remaining_work))
        proposer = OfflineDispatchProposer(
            adversarial="wrong_handler" if provider_name == "adversarial" else None,
            max_work=limit,
        )
    from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider

    return GuardedDispatchProvider(snapshot, options, proposer, remaining_work=remaining_work)
