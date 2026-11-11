"""Deterministic offline L2 proposers and an explicitly enabled factory.

Proposers deliberately do not validate source semantics. The trusted checker
rebuilds the complete source obligations independently after every proposal.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import time

from pythonstan.analysis.contracts import SemanticIdentity, VersionedDependency
from .candidates import CandidateProposal


def _ast_digest(node):
    return hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()


class OfflineDispatchProposer:
    def __init__(self, *, coarse=False, adversarial=None, max_work=100000):
        self.coarse,self.adversarial,self.max_work = coarse,adversarial,max_work

    def propose(self,source, *, identity=None,max_work=None):
        from pythonstan.analysis.verification.guarded_dispatch.language import (
            PROFILE, DispatchArm, FiniteGuard, GuardedSummary, HandlerClaim, candidate_to_dict)
        from pythonstan.analysis.verification.guarded_dispatch.obligations import MODEL_DIGEST
        started = time.monotonic()
        work = 0
        try:
            if type(source) is not str or len(source.encode()) > 1_000_000:
                return CandidateProposal("budget_exhausted",None,"offline",0,time.monotonic()-started,("source budget",))
            tree = ast.parse(source,type_comments=True,feature_version=(3,12))
            work = sum(1 for _ in ast.walk(tree))
            limit = self.max_work if max_work is None else min(self.max_work,max(0,max_work))
            if work > limit:
                return CandidateProposal("budget_exhausted",None,"offline",limit,time.monotonic()-started,("proposal work budget",))
            functions = [node for node in tree.body if isinstance(node,ast.FunctionDef)]
            dispatch = next(node for node in functions if len(node.args.args) == 3)
            table_node = next(node for node in tree.body if isinstance(node,ast.Assign) and isinstance(node.value,ast.Dict))
            table_name = table_node.targets[0].id
            table = tuple((key.value,value.id) for key,value in zip(table_node.value.keys,table_node.value.values))
            handlers = []
            for function in functions:
                if function is dispatch:
                    continue
                returns = [node for node in ast.walk(function) if isinstance(node,ast.Return)]
                relation = "fresh_box" if returns and isinstance(returns[-1].value,ast.Dict) else "input"
                if self.coarse:
                    relation = "top"
                handlers.append(HandlerClaim(function.name,relation,function.args.args[0].arg,
                                             "value" if relation == "fresh_box" else None))
            claims = {handler.name:handler for handler in handlers}
            if identity is None:
                dependencies = tuple(sorted(
                    [VersionedDependency("function",function.name,_ast_digest(function)) for function in functions]
                    + [VersionedDependency("table",table_name,_ast_digest(table_node)),
                       VersionedDependency("model",PROFILE,MODEL_DIGEST)],
                    key=lambda item:(item.kind,item.identity)))
                identity = SemanticIdentity(hashlib.sha256(source.encode()).hexdigest(),_ast_digest(tree),
                    MODEL_DIGEST,"default-policy",PROFILE,dependencies=dependencies)
            candidate = GuardedSummary(identity,dispatch.name,FiniteGuard(table_name,tuple(key for key,_ in table)),
                tuple(handlers),tuple(DispatchArm(key,handler,claims[handler].relation) for key,handler in table))
            wire = candidate_to_dict(candidate)
            if self.adversarial == "wrong_handler":
                alternatives = [h.name for h in handlers if h.name != wire["arms"][0]["handler"]]
                wire["arms"][0]["handler"] = alternatives[0] if alternatives else "invented_handler"
            elif self.adversarial == "missing_handler":
                wire["handlers"] = wire["handlers"][:-1]
            elif self.adversarial == "wrong_fresh":
                for arm in wire["arms"]:
                    if arm["relation"] == "fresh_box":
                        arm["relation"] = "input"
                        break
            elif self.adversarial == "stale_ir":
                wire["identity"]["ir_digest"] = "stale"
            elif self.adversarial == "self_verified":
                wire["verified"] = True
            return CandidateProposal("candidate",wire,"adversarial" if self.adversarial else "offline",work,time.monotonic()-started)
        except (SyntaxError,ValueError,AttributeError,IndexError,KeyError,StopIteration,RecursionError) as error:
            return CandidateProposal("unavailable",None,"offline",work,time.monotonic()-started,(str(error),))


class RecordedDispatchProposer:
    def __init__(self,recordings):
        self.recordings = deepcopy(recordings)

    def propose(self,source, *, identity=None,max_work=None):
        started = time.monotonic()
        if max_work is not None and max_work < 1:
            return CandidateProposal("budget_exhausted",None,"recorded",0,time.monotonic()-started,("proposal work budget",))
        digest = hashlib.sha256(source.encode()).hexdigest()
        candidate = self.recordings.get(digest)
        return CandidateProposal("candidate" if candidate is not None else "unavailable",deepcopy(candidate),
                                 "recorded",1,time.monotonic()-started)


def make_summary_provider(snapshot,config,remaining_work=None):
    """Session orchestration factory; disabled LLM needs no provider at all."""
    options = config.to_dict() if hasattr(config,"to_dict") else dict(config)
    llm = options.get("llm",{})
    if not llm.get("enabled") or not llm.get("level2"):
        return None
    provider_name = llm.get("provider","offline")
    if provider_name == "recorded":
        recordings = {}
        for record in llm.get("proposals",()):
            if isinstance(record,dict) and record.get("level") == 2 and "source_digest" in record:
                recordings[record["source_digest"]] = record.get("candidate")
        proposer = RecordedDispatchProposer(recordings)
    else:
        limit = options.get("resources",{}).get("max_work",100000)
        if remaining_work is not None:
            limit = min(limit,max(0,remaining_work))
        proposer = OfflineDispatchProposer(adversarial="wrong_handler" if provider_name == "adversarial" else None,
                                          max_work=limit)
    from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider
    return GuardedDispatchProvider(snapshot,options,proposer,remaining_work=remaining_work)
