"""Independent source refinement checker; proposals never control obligations."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import secrets
from typing import Mapping

from pythonstan.analysis.contracts import SemanticIdentity
from pythonstan.analysis.contracts.identity import stable_digest
from pythonstan.analysis.contracts import thaw_snapshot
from .language import (CandidateError, GuardedSummary, candidate_to_dict, parse_candidate)
from .obligations import (ProgramFacts, UnsupportedSource, VerificationBudget,
                          VerificationUnknown, WorkMeter, generate_obligations)

CHECKER_VERSION = "guarded-dispatch-source-refinement-1"
_CERTIFICATE_KEY = secrets.token_bytes(32)


def _certificate_digest(candidate, facts):
    payload = {"checker":CHECKER_VERSION,"identity":facts.identity.digest,
               "candidate":candidate_to_dict(candidate),"dispatch":facts.dispatch,
               "parameters":facts.dispatch_parameters,"table_name":facts.table_name,
               "table":facts.table,"table_digest":facts.table_digest,
               "handlers":[(h.name,h.parameter,h.relation,h.allocation,h.digest) for h in facts.handlers],
               "calls":[(c.target,c.point,c.key,thaw_snapshot(c.payload),c.reachable) for c in facts.calls],
               "source_nodes":facts.source_nodes,"complete_source":True}
    return stable_digest(payload)


def _certificate_stamp(candidate, facts, proof_digest):
    # The records and all nested payloads are immutable. Bind their identities
    # once, so repeated applications do not revalidate an O(program-size)
    # vector. Replacing a frozen record loses this host-only authority stamp.
    value = f"{id(candidate)}:{id(facts)}:{proof_digest}:{CHECKER_VERSION}".encode()
    return hmac.new(_CERTIFICATE_KEY,value,hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class VerifiedSummary:
    candidate: GuardedSummary
    source_facts: ProgramFacts
    proof_digest: str
    checked_work: int
    checker_version: str = CHECKER_VERSION
    _seal: object = field(default=None, repr=False, compare=False)

    @property
    def issued_by_checker(self):
        try:
            return (isinstance(self._seal,str) and self.checker_version == CHECKER_VERSION
                    and hmac.compare_digest(self._seal,_certificate_stamp(self.candidate,self.source_facts,self.proof_digest)))
        except (ValueError,TypeError,AttributeError):
            return False


@dataclass(frozen=True)
class VerificationResult:
    status: str
    diagnostics: tuple[str, ...] = ()
    certificate: VerifiedSummary | None = None
    checked_work: int = 0
    checked_functions: tuple[str, ...] = ()
    source_coverage: bool = False
    counters: tuple[tuple[str,int], ...] = ()

    @property
    def certified(self):
        return self.status == "accepted" and self.certificate is not None and self.certificate.issued_by_checker


def identity_for_source(source, *, policy_digest="default-policy", state_version="0", budget=None):
    """Trusted profile identity; this creates no certificate or caller evidence."""
    return generate_obligations(source,policy_digest=policy_digest,state_version=state_version,budget=budget).identity


def check_source_candidate(source, candidate, *, identity=None, budget=None):
    budget = budget or VerificationBudget()
    meter = WorkMeter(budget)
    try:
        proposal = parse_candidate(candidate)
    except (CandidateError,TypeError,ValueError,AttributeError,KeyError,RecursionError) as error:
        return VerificationResult("rejected",("malformed candidate: " + str(error),))
    if identity is not None and not isinstance(identity,SemanticIdentity):
        return VerificationResult("rejected",("expected identity must be a trusted SemanticIdentity",))
    policy = identity.policy_digest if identity is not None else "default-policy"
    state = identity.state_version if identity is not None else "0"
    try:
        facts = generate_obligations(source,policy_digest=policy,state_version=state,budget=budget,meter=meter)
        if identity is not None and facts.identity != identity:
            return VerificationResult("rejected",("current source/IR/model/policy/dependency identity mismatch",),checked_work=meter.work)
        if proposal.identity != facts.identity:
            return VerificationResult("rejected",("candidate source/IR/model/policy/dependency identity mismatch",),checked_work=meter.work)
        names = tuple(sorted(handler.name for handler in facts.handlers))
        claims = {handler.name:handler for handler in proposal.handlers}
        if set(claims) != set(names):
            return VerificationResult("rejected",("candidate must cover the complete source handler vector",),checked_work=meter.work,checked_functions=names,source_coverage=True)
        if (proposal.dispatch != facts.dispatch or proposal.guard.table_name != facts.table_name
                or set(proposal.guard.keys) != {key for key,_ in facts.table}):
            return VerificationResult("rejected",("candidate guard/table/dispatch does not cover source",),checked_work=meter.work,checked_functions=names,source_coverage=True)
        arms = {arm.key:arm for arm in proposal.arms}
        if set(arms) != {key for key,_ in facts.table}:
            return VerificationResult("rejected",("candidate omits or invents key arms",),checked_work=meter.work,checked_functions=names,source_coverage=True)
        source_handlers = {handler.name:handler for handler in facts.handlers}
        failures = []
        for actual in facts.handlers:
            meter.tick("handler_vector_obligations")
            claim = claims[actual.name]
            if (claim.input_parameter != actual.parameter or claim.relation not in (actual.relation,"top")
                    or not claim.normal_exit or claim.exceptions or claim.suspends
                    or claim.existing_heap != "unchanged"):
                failures.append(actual.name + ": return/frame/outcome refinement failed")
        for key,handler in facts.table:
            meter.tick("key_handler_obligations")
            arm = arms[key]
            if arm.handler != handler or arm.relation not in (source_handlers[handler].relation,"top"):
                failures.append(key + ": key-handler-return relation refinement failed")
        # Source calls outside a candidate guard are not deleted. They are
        # retained in source_facts so guard/application computes a complement.
        meter.tick("source_call_obligations",len(facts.calls))
        if failures:
            return VerificationResult("rejected",tuple(failures),checked_work=meter.work,
                                      checked_functions=names,source_coverage=True,counters=tuple(sorted(meter.counts.items())))
        digest = _certificate_digest(proposal,facts)
        certificate = VerifiedSummary(proposal,facts,digest,meter.work,
                                      _seal=_certificate_stamp(proposal,facts,digest))
        return VerificationResult("accepted",(),certificate,meter.work,names,True,tuple(sorted(meter.counts.items())))
    except UnsupportedSource as error:
        return VerificationResult("unsupported",(str(error),),checked_work=meter.work,counters=tuple(sorted(meter.counts.items())))
    except (VerificationUnknown,RecursionError) as error:
        # A partially checked vector is never an issued certificate.
        return VerificationResult("unknown",(str(error),),checked_work=meter.work,counters=tuple(sorted(meter.counts.items())))


def recheck_certificate(source, certificate, *, identity=None, budget=None):
    """Durable/cache candidate data is always independently checked again."""
    if not isinstance(certificate,VerifiedSummary) or not certificate.issued_by_checker:
        return VerificationResult("rejected",("not a checker-issued summary",))
    return check_source_candidate(source,certificate.candidate,identity=identity,budget=budget)
