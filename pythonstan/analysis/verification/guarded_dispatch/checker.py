"""Independent source refinement checker; proposals never control obligations."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field

from pythonstan.analysis.contracts import SemanticIdentity, thaw_snapshot
from pythonstan.analysis.contracts.identity import stable_digest
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger, WorkReservation

from .language import (
    CandidateError,
    GuardedSummary,
    candidate_to_dict,
    identity_to_dict,
    parse_candidate,
)
from .obligations import (
    ProgramFacts,
    UnsupportedSource,
    VerificationBudget,
    VerificationUnknown,
    WorkMeter,
    generate_obligations,
    verification_meter,
)

CHECKER_VERSION = "guarded-dispatch-source-refinement-1"
_CERTIFICATE_KEY = secrets.token_bytes(32)


def _certificate_digest(candidate: GuardedSummary, facts: ProgramFacts, meter: WorkMeter) -> str:
    with meter.traversal("certificate_candidate_copy") as visit:
        candidate_wire = candidate_to_dict(candidate, visit=visit)
    with meter.traversal("certificate_identity_copy") as visit:
        identity_wire = identity_to_dict(facts.identity, visit=visit)
    meter.admit_json(identity_wire, "certificate_identity_hash")
    with meter.traversal("certificate_source_vector") as visit:
        visit(1 + 6 * len(facts.handlers) + 6 * len(facts.calls))
    with meter.traversal("certificate_call_payloads") as visit:
        calls = [
            (c.target, c.point, c.key, thaw_snapshot(c.payload, visit=visit), c.reachable)
            for c in facts.calls
        ]
    payload = {
        "checker": CHECKER_VERSION,
        "identity": facts.identity.digest,
        "candidate": candidate_wire,
        "dispatch": facts.dispatch,
        "parameters": facts.dispatch_parameters,
        "table_name": facts.table_name,
        "table": facts.table,
        "table_digest": facts.table_digest,
        "handlers": [
            (h.name, h.parameter, h.relation, h.allocation, h.digest) for h in facts.handlers
        ],
        "calls": calls,
        "source_nodes": facts.source_nodes,
        "complete_source": True,
    }
    meter.admit_json(payload, "certificate_json_hash")
    return stable_digest(payload)


def _certificate_stamp(candidate: GuardedSummary, facts: ProgramFacts, proof_digest: str) -> str:
    # The records and all nested payloads are immutable. Bind their identities
    # once, so repeated applications do not revalidate an O(program-size)
    # vector. Replacing a frozen record loses this host-only authority stamp.
    value = f"{id(candidate)}:{id(facts)}:{proof_digest}:{CHECKER_VERSION}".encode()
    return hmac.new(_CERTIFICATE_KEY, value, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class VerifiedSummary:
    candidate: GuardedSummary
    source_facts: ProgramFacts
    proof_digest: str
    checked_work: int
    checker_version: str = CHECKER_VERSION
    _seal: object = field(default=None, repr=False, compare=False)

    @property
    def issued_by_checker(self) -> bool:
        try:
            return (
                isinstance(self._seal, str)
                and self.checker_version == CHECKER_VERSION
                and hmac.compare_digest(
                    self._seal,
                    _certificate_stamp(self.candidate, self.source_facts, self.proof_digest),
                )
            )
        except (ValueError, TypeError, AttributeError):
            return False


@dataclass(frozen=True)
class VerificationResult:
    status: str
    diagnostics: tuple[str, ...] = ()
    certificate: VerifiedSummary | None = None
    checked_work: int = 0
    checked_functions: tuple[str, ...] = ()
    source_coverage: bool = False
    counters: tuple[tuple[str, int], ...] = ()

    @property
    def certified(self) -> bool:
        return (
            self.status == "accepted"
            and self.certificate is not None
            and self.certificate.issued_by_checker
        )


def identity_for_source(
    source: object,
    *,
    policy_digest: str = "default-policy",
    state_version: str = "0",
    budget: VerificationBudget | None = None,
    resource_ledger: RunLedger | None = None,
    work_reservation: WorkReservation | None = None,
) -> SemanticIdentity:
    """Trusted profile identity; this creates no certificate or caller evidence."""
    return generate_obligations(
        source,
        policy_digest=policy_digest,
        state_version=state_version,
        budget=budget,
        resource_ledger=resource_ledger,
        work_reservation=work_reservation,
    ).identity


def check_source_candidate(
    source: object,
    candidate: object,
    *,
    identity: object = None,
    budget: VerificationBudget | None = None,
    resource_ledger: RunLedger | None = None,
    work_reservation: WorkReservation | None = None,
) -> VerificationResult:
    budget = budget or VerificationBudget()
    meter: WorkMeter | None = None
    try:
        with verification_meter(
            budget, resource_ledger=resource_ledger, work_reservation=work_reservation
        ) as meter:
            return _check_candidate(source, candidate, identity, budget, meter)
    except (VerificationUnknown, ResourceStop) as error:
        return VerificationResult(
            "unknown",
            (str(error),),
            checked_work=0 if meter is None else meter.work,
            counters=() if meter is None else tuple(sorted(meter.counts.items())),
        )
    except MemoryError:
        if resource_ledger is not None:
            resource_ledger.stop("max_memory_mb")
        return VerificationResult(
            "unknown",
            ("checker memory unavailable",),
            checked_work=0 if meter is None else meter.work,
            counters=() if meter is None else tuple(sorted(meter.counts.items())),
        )


def _same_identity(left: SemanticIdentity, right: SemanticIdentity, meter: WorkMeter) -> bool:
    with meter.traversal("checker_identity_comparison") as visit:
        identity_to_dict(left, visit=visit)
        identity_to_dict(right, visit=visit)
    return left == right


def _check_candidate(
    source: object,
    candidate: object,
    identity: object,
    budget: VerificationBudget,
    meter: WorkMeter,
) -> VerificationResult:
    try:
        with meter.traversal("checker_candidate_parse") as visit:
            proposal = parse_candidate(candidate, visit=visit)
    except (
        CandidateError,
        TypeError,
        ValueError,
        AttributeError,
        KeyError,
        RecursionError,
    ) as error:
        return VerificationResult(
            "rejected",
            ("malformed candidate: " + str(error),),
            checked_work=meter.work,
            counters=tuple(sorted(meter.counts.items())),
        )
    if identity is not None and not isinstance(identity, SemanticIdentity):
        return VerificationResult(
            "rejected",
            ("expected identity must be a trusted SemanticIdentity",),
            checked_work=meter.work,
            counters=tuple(sorted(meter.counts.items())),
        )
    policy = identity.policy_digest if identity is not None else "default-policy"
    state = identity.state_version if identity is not None else "0"
    try:
        facts = generate_obligations(
            source, policy_digest=policy, state_version=state, budget=budget, meter=meter
        )
        if identity is not None and not _same_identity(facts.identity, identity, meter):
            return VerificationResult(
                "rejected",
                ("current source/IR/model/policy/dependency identity mismatch",),
                checked_work=meter.work,
            )
        if not _same_identity(proposal.identity, facts.identity, meter):
            return VerificationResult(
                "rejected",
                ("candidate source/IR/model/policy/dependency identity mismatch",),
                checked_work=meter.work,
            )
        with meter.traversal("checker_claim_vectors") as visit:
            visit(1 + len(facts.handlers) + len(proposal.handlers) + len(proposal.arms))
        names = tuple(sorted(handler.name for handler in facts.handlers))
        claims = {handler.name: handler for handler in proposal.handlers}
        if set(claims) != set(names):
            return VerificationResult(
                "rejected",
                ("candidate must cover the complete source handler vector",),
                checked_work=meter.work,
                checked_functions=names,
                source_coverage=True,
            )
        if (
            proposal.dispatch != facts.dispatch
            or proposal.guard.table_name != facts.table_name
            or set(proposal.guard.keys) != {key for key, _ in facts.table}
        ):
            return VerificationResult(
                "rejected",
                ("candidate guard/table/dispatch does not cover source",),
                checked_work=meter.work,
                checked_functions=names,
                source_coverage=True,
            )
        arms = {arm.key: arm for arm in proposal.arms}
        if set(arms) != {key for key, _ in facts.table}:
            return VerificationResult(
                "rejected",
                ("candidate omits or invents key arms",),
                checked_work=meter.work,
                checked_functions=names,
                source_coverage=True,
            )
        source_handlers = {handler.name: handler for handler in facts.handlers}
        failures = []
        for actual in facts.handlers:
            meter.tick("handler_vector_obligations")
            claim = claims[actual.name]
            if (
                claim.input_parameter != actual.parameter
                or claim.relation not in (actual.relation, "top")
                or not claim.normal_exit
                or claim.exceptions
                or claim.suspends
                or claim.existing_heap != "unchanged"
            ):
                failures.append(actual.name + ": return/frame/outcome refinement failed")
        for key, handler in facts.table:
            meter.tick("key_handler_obligations")
            arm = arms[key]
            if arm.handler != handler or arm.relation not in (
                source_handlers[handler].relation,
                "top",
            ):
                failures.append(key + ": key-handler-return relation refinement failed")
        # Source calls outside a candidate guard are not deleted. They are
        # retained in source_facts so guard/application computes a complement.
        for _ in facts.calls:
            meter.tick("source_call_obligations")
        if failures:
            return VerificationResult(
                "rejected",
                tuple(failures),
                checked_work=meter.work,
                checked_functions=names,
                source_coverage=True,
                counters=tuple(sorted(meter.counts.items())),
            )
        digest = _certificate_digest(proposal, facts, meter)
        meter.tick("certificate_host_seal")
        certificate = VerifiedSummary(
            proposal, facts, digest, meter.work, _seal=_certificate_stamp(proposal, facts, digest)
        )
        meter.checkpoint(force=True)
        return VerificationResult(
            "accepted",
            (),
            certificate,
            meter.work,
            names,
            True,
            tuple(sorted(meter.counts.items())),
        )
    except UnsupportedSource as error:
        return VerificationResult(
            "unsupported",
            (str(error),),
            checked_work=meter.work,
            counters=tuple(sorted(meter.counts.items())),
        )
    except (VerificationUnknown, RecursionError) as error:
        # A partially checked vector is never an issued certificate.
        return VerificationResult(
            "unknown",
            (str(error),),
            checked_work=meter.work,
            counters=tuple(sorted(meter.counts.items())),
        )


def recheck_certificate(
    source: object,
    certificate: object,
    *,
    identity: object = None,
    budget: VerificationBudget | None = None,
    resource_ledger: RunLedger | None = None,
    work_reservation: WorkReservation | None = None,
) -> VerificationResult:
    """Durable/cache candidate data is always independently checked again."""
    if not isinstance(certificate, VerifiedSummary) or not certificate.issued_by_checker:
        return VerificationResult("rejected", ("not a checker-issued summary",))
    return check_source_candidate(
        source,
        certificate.candidate,
        identity=identity,
        budget=budget,
        resource_ledger=resource_ledger,
        work_reservation=work_reservation,
    )
