"""Deterministic offline L2 proposers and an explicitly enabled factory.

Proposers deliberately do not validate source semantics. The trusted checker
rebuilds the complete source obligations independently after every proposal.
"""

from __future__ import annotations

import ast
import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts import SemanticIdentity, SourceModule, VersionedDependency
from pythonstan.analysis.contracts.cost_model import visit_json_text
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_array,
    require_bool,
    require_int,
    require_object,
    require_string,
    validate_json_object,
)
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger, WorkReservation
from pythonstan.config import EffectiveConfig
from pythonstan.config.errors import ConfigurationError
from pythonstan.config.llm_provider import ProviderMode, ProviderOptions

from .candidates import CandidateProposal, CandidateProvider, SourceBundle

if TYPE_CHECKING:
    from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider


def _ast_digest(node: ast.AST, spend: Callable[[int], None] | None = None) -> str:
    if spend is not None:
        for _ in ast.walk(node):
            spend(1)
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def _dependency_key(item: VersionedDependency) -> tuple[str, str]:
    return item.kind, item.identity


def _literal_table(
    node: ast.Assign, spend: Callable[[int], None] | None = None
) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Recognize the proposer's finite string-table syntax without trusting it."""
    if not isinstance(node.value, ast.Dict) or not node.targets:
        raise ValueError("No finite dispatch table")
    target = node.targets[0]
    if not isinstance(target, ast.Name):
        raise ValueError("Dispatch table needs a named binding")
    table: list[tuple[str, str]] = []
    for key, value in zip(node.value.keys, node.value.values, strict=True):
        if spend is not None:
            spend(1)
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
        self,
        *,
        coarse: bool = False,
        adversarial: str | None = None,
        max_work: int = 100000,
        resource_ledger: RunLedger | None = None,
    ) -> None:
        self.coarse: bool = coarse
        self.adversarial: str | None = adversarial
        self.max_work: int = max_work
        self.provider_name = "adversarial" if adversarial else "offline"
        self.resource_ledger = (
            RunLedger(max_work, 0, 0) if resource_ledger is None else resource_ledger
        )

    def propose(
        self,
        source: str,
        *,
        identity: SemanticIdentity | None = None,
        max_work: int | None = None,
        work_reservation: WorkReservation | None = None,
    ) -> CandidateProposal:
        started = time.monotonic()
        grant = work_reservation
        before = 0 if grant is None else grant.observed
        try:
            limit = self.max_work if max_work is None else min(self.max_work, max(0, max_work))
            if grant is None:
                grant = self.resource_ledger.reserve_work(
                    min(limit, self.resource_ledger.remaining_work), category="offline_proposer_cpu"
                )
            grant.consume(0, owner=self.resource_ledger)
            before = grant.observed

            def spend(amount: int) -> None:
                if amount > limit - (grant.observed - before):
                    raise ResourceStop("proposal_max_work")
                grant.consume(amount, owner=self.resource_ledger)

            result = self._build_proposal(source, identity=identity, max_work=limit, spend=spend)
            return replace(result, work=grant.observed - before)
        except ResourceStop:
            return CandidateProposal(
                "budget_exhausted",
                None,
                self.provider_name,
                0 if grant is None else grant.observed - before,
                time.monotonic() - started,
                ("proposal work budget",),
            )
        except (TypeError, ValueError, UnicodeError, RecursionError):
            return CandidateProposal(
                "unavailable",
                None,
                self.provider_name,
                0 if grant is None else grant.observed - before,
                time.monotonic() - started,
                ("proposal input is unavailable",),
            )
        finally:
            if work_reservation is None and grant is not None and grant.status == "reserved":
                grant.settle(grant.observed)

    def _build_proposal(
        self,
        source: str,
        *,
        identity: SemanticIdentity | None,
        max_work: int,
        spend: Callable[[int], None],
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
            spend(1)
            with self.resource_ledger.structural_meter("offline_source", charge=spend) as meter:
                visit_json_text(source, meter.visit)
            if type(source) is not str or len(source.encode()) > 1_000_000:
                return CandidateProposal(
                    "budget_exhausted",
                    None,
                    self.provider_name,
                    0,
                    time.monotonic() - started,
                    ("source budget",),
                )
            spend(1)
            tree = ast.parse(source, type_comments=True, feature_version=(3, 12))
            for _ in ast.walk(tree):
                spend(1)
                work += 1
            limit = self.max_work if max_work is None else min(self.max_work, max(0, max_work))
            if work > limit:
                return CandidateProposal(
                    "budget_exhausted",
                    None,
                    self.provider_name,
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
            table_name, table = _literal_table(table_node, spend)
            handlers = []
            for function in functions:
                spend(1)
                if function is dispatch:
                    continue
                returns = []
                for node in ast.walk(function):
                    spend(1)
                    if isinstance(node, ast.Return):
                        returns.append(node)
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
                            VersionedDependency(
                                "function", function.name, _ast_digest(function, spend)
                            )
                            for function in functions
                        ]
                        + [
                            VersionedDependency(
                                "table", table_name, _ast_digest(table_node, spend)
                            ),
                            VersionedDependency("model", PROFILE, MODEL_DIGEST),
                        ],
                        key=_dependency_key,
                    )
                )
                identity = SemanticIdentity(
                    hashlib.sha256(source.encode()).hexdigest(),
                    _ast_digest(tree, spend),
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
            with self.resource_ledger.structural_meter("offline_candidate", charge=spend) as meter:
                wire = candidate_to_dict(candidate, visit=meter.visit)
            _adversarial_change(wire, self.adversarial)
            return CandidateProposal(
                "candidate",
                wire,
                self.provider_name,
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
                "unavailable",
                None,
                self.provider_name,
                work,
                time.monotonic() - started,
                (str(error),),
            )


class RecordedDispatchProposer:
    def __init__(
        self,
        recordings: Mapping[str, object],
        *,
        provider_name: str = "recorded",
        resource_ledger: RunLedger | None = None,
    ) -> None:
        self.recordings: Mapping[str, object] = recordings
        self.provider_name = provider_name
        self.resource_ledger = (
            RunLedger(100000, 0, 0) if resource_ledger is None else resource_ledger
        )

    def propose(
        self,
        source: str,
        *,
        identity: SemanticIdentity | None = None,
        max_work: int | None = None,
        work_reservation: WorkReservation | None = None,
    ) -> CandidateProposal:
        started = time.monotonic()
        grant = work_reservation
        before = 0
        try:
            limit = self.resource_ledger.remaining_work if grant is None else grant.held
            if max_work is not None:
                limit = min(limit, max(0, max_work))
            if grant is None:
                grant = self.resource_ledger.reserve_work(limit, category="recorded_proposer_cpu")
            grant.consume(0, owner=self.resource_ledger)
            before = grant.observed

            def spend(amount: int) -> None:
                if amount > limit - (grant.observed - before):
                    raise ResourceStop("proposal_max_work")
                grant.consume(amount, owner=self.resource_ledger)

            spend(1)
            with self.resource_ledger.structural_meter("recorded_source", charge=spend) as meter:
                visit_json_text(source, meter.visit)
            digest = hashlib.sha256(source.encode()).hexdigest()
            candidate = self.recordings.get(digest)
            from pythonstan.analysis.contracts.wire import freeze_snapshot, thaw_snapshot

            if candidate is not None:
                with self.resource_ledger.structural_meter(
                    "recorded_candidate", charge=spend
                ) as meter:
                    candidate = thaw_snapshot(
                        freeze_snapshot(candidate, visit=meter.visit), visit=meter.visit
                    )
            return CandidateProposal(
                "candidate" if candidate is not None else "unavailable",
                candidate,
                self.provider_name,
                grant.observed - before,
                time.monotonic() - started,
            )
        except ResourceStop:
            return CandidateProposal(
                "budget_exhausted",
                None,
                self.provider_name,
                0 if grant is None else grant.observed - before,
                time.monotonic() - started,
                ("proposal work budget",),
            )
        except (TypeError, ValueError, UnicodeError, RecursionError):
            return CandidateProposal(
                "unavailable",
                None,
                self.provider_name,
                0 if grant is None else grant.observed - before,
                time.monotonic() - started,
                ("recorded candidate is not bounded JSON",),
            )
        finally:
            if work_reservation is None and grant is not None and grant.status == "reserved":
                grant.settle(grant.observed)


def make_summary_provider(
    snapshot: SourceBundle | Mapping[str, SourceModule],
    config: EffectiveConfig | Mapping[str, object],
    remaining_work: int | None = None,
    *,
    resource_ledger: RunLedger | None = None,
) -> GuardedDispatchProvider | None:
    """Assemble only explicitly enabled L2; proposals retain no trust authority."""
    if resource_ledger is None:
        options = (
            config.to_dict()
            if isinstance(config, EffectiveConfig)
            else validate_json_object(config)
        )
    else:
        from pythonstan.analysis.contracts import freeze_snapshot, thaw_snapshot

        resource_ledger.charge_work(1, "provider_configuration_dispatch")
        with resource_ledger.structural_meter("provider_configuration_copy") as meter:
            options = (
                config.to_dict(visit=meter.visit)
                if isinstance(config, EffectiveConfig)
                else require_object(
                    thaw_snapshot(freeze_snapshot(config, visit=meter.visit), visit=meter.visit)
                )
            )
    llm = require_object(options.get("llm", {}))
    if not require_bool(llm.get("enabled", False)) or not require_bool(llm.get("level2", False)):
        return None
    provider_name = require_string(llm.get("provider", "offline"))
    provider_options = ProviderOptions.from_wire(llm)
    proposer: CandidateProvider
    if provider_options.mode is ProviderMode.DISABLED:
        return None
    if provider_options.mode is ProviderMode.OPENAI_LIVE:
        from pythonstan.analysis.contracts import stable_digest

        from .responses import ResponsesCandidateProposer

        proposer = ResponsesCandidateProposer(
            provider_options, policy_digest=stable_digest(options), resource_ledger=resource_ledger
        )
    elif provider_options.mode is ProviderMode.REPLAY:
        recordings: dict[str, object] = {}
        for record in require_array(llm.get("proposals", [])):
            if isinstance(record, dict) and record.get("level") == 2:
                digest = record.get("source_digest")
                if isinstance(digest, str):
                    recordings[digest] = record.get("candidate")
        proposer = RecordedDispatchProposer(
            recordings, provider_name=provider_name, resource_ledger=resource_ledger
        )
    elif provider_options.mode in (ProviderMode.OFFLINE, ProviderMode.ADVERSARIAL):
        limit = require_int(require_object(options.get("resources", {})).get("max_work", 100000))
        if remaining_work is not None:
            limit = min(limit, max(0, remaining_work))
        proposer = OfflineDispatchProposer(
            adversarial="wrong_handler" if provider_name == "adversarial" else None,
            max_work=limit,
            resource_ledger=resource_ledger,
        )
    else:
        raise ConfigurationError("Unsupported candidate provider")
    from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider

    provider = GuardedDispatchProvider(
        snapshot, options, proposer, remaining_work=remaining_work, resource_ledger=resource_ledger
    )
    provider.prepare()
    return provider
