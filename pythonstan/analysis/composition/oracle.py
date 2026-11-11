"""A run-local incremental may-inclusion oracle, never an exclusion oracle.

The same finite delta/replay kernel is used by bounded Native regions. Values
carry engine-neutral object IDs. Partial or empty candidates cannot invalidate
an independently discovered abstract behavior.
"""
from __future__ import annotations
from collections import defaultdict,deque
from dataclasses import dataclass
from typing import Any
from pythonstan.analysis.domains.values import Value,ValueOperations
from pythonstan.analysis.contracts import stable_digest

@dataclass(frozen=True)
class CandidateAnswer:
    values: Value
    coverage: str
    version: int
    provenance: tuple[str,...]
    identity: str
    def permits_exclusion(self):return False

class IncrementalInclusionStore:
    def __init__(self,ops=None):
        self.ops=ops or ValueOperations();self.facts={};self.edges=defaultdict(set)
        self.loads=defaultdict(set);self.stores=defaultdict(set);self.operations=defaultdict(list)
        self.queue=deque();self.queued=set();self.listeners=defaultdict(list);self.version=0
        self.work=0;self.publications=0;self.deliveries=0;self.replays=0;self.origins=defaultdict(set);self.errors=set()
    def add_fact(self,node,value,origin="input"):
        old=self.facts.get(node,Value.bottom());new=self.ops.join(old,value)
        if self.ops.leq(new,old) and new.refs<=old.refs and new.relations<=old.relations:return False
        self.facts[node]=new;self.origins[node].add(origin);self.version+=1;self.publications+=1
        if node not in self.queued:self.queued.add(node);self.queue.append(node)
        return True
    def add_edge(self,source,target):
        if target in self.edges[source]:return False
        self.edges[source].add(target);self.replays+=1
        self.add_fact(target,self.facts.get(source,Value.bottom()),"late_edge_replay")
        return True
    def add_load(self,base,key,target):
        rule=(key,target)
        if rule in self.loads[base]:return
        self.loads[base].add(rule)
        for ref in self.facts.get(base,Value.bottom()).refs:self.add_edge(("field",ref,key),target)
    def add_store(self,base,key,source):
        rule=(key,source)
        if rule in self.stores[base]:return
        self.stores[base].add(rule)
        for ref in self.facts.get(base,Value.bottom()).refs:self.add_edge(source,("field",ref,key))
    def add_operation(self,inputs,target,function):
        rule=(tuple(inputs),target,function)
        for source in inputs:self.operations[source].append(rule)
        self._operation(rule)
    def _operation(self,rule):
        inputs,target,function=rule;values=[self.facts.get(x,Value.bottom()) for x in inputs]
        if any(x.is_bottom() for x in values):return
        value,errors=function(*values);self.errors.update(errors);self.add_fact(target,value,"scalar_transfer")
    def subscribe(self,node,callback):
        if callback not in self.listeners[node]:self.listeners[node].append(callback)
    def solve(self,max_work=None):
        selected=0
        while self.queue and (max_work is None or selected<max_work):
            node=self.queue.popleft();self.queued.discard(node);value=self.facts[node];selected+=1;self.work+=1
            for target in tuple(self.edges[node]):self.add_fact(target,value,"copy_constraint")
            for key,target in tuple(self.loads[node]):
                for ref in value.refs:self.add_edge(("field",ref,key),target)
            for key,source in tuple(self.stores[node]):
                for ref in value.refs:self.add_edge(source,("field",ref,key))
            for rule in tuple(self.operations[node]):self._operation(rule)
            for callback in tuple(self.listeners[node]):callback(node,self.version)
        return not self.queue
    def value(self,node):self.deliveries+=1;return self.facts.get(node,Value.bottom())
    def statistics(self):return {"work":self.work,"facts":len(self.facts),"edges":sum(map(len,self.edges.values())),"publications":self.publications,"deliveries":self.deliveries,"late_edge_replays":self.replays,"pending":len(self.queue),"version":self.version}

class IncrementalCandidateOracle:
    def __init__(self,*,source_digest,profile,policy_digest="",model_digest="pythonstan-builtins-v1",ops=None,max_work=100000):
        self.identity=stable_digest({"source":source_digest,"profile":profile,"policy":policy_digest,"model":model_digest})
        self.source_digest=source_digest;self.profile=profile;self.policy_digest=policy_digest;self.model_digest=model_digest
        self.kernel=IncrementalInclusionStore(ops);self.max_work=max_work;self.driver=None
        self.actual_consumptions=0;self.nonempty_consumptions=0;self.calls=set();self._subscriptions=set()
    def bind(self,driver,*,source_digest=None,profile=None,policy_digest=None):
        actual_source=driver.source_digest
        if source_digest is not None and source_digest!=actual_source:raise ValueError("oracle source identity override mismatch")
        actual_profile=driver.options.get("semantic_profile","python-core-v1")
        if profile is not None and profile!=actual_profile:raise ValueError("oracle profile identity override mismatch")
        actual_policy=driver.policy_digest
        if policy_digest is not None and policy_digest!=actual_policy:raise ValueError("oracle policy identity override mismatch")
        if actual_source!=self.source_digest:raise ValueError("oracle source identity mismatch")
        if actual_profile!=self.profile:raise ValueError("oracle profile identity mismatch")
        if self.policy_digest and actual_policy!=self.policy_digest:raise ValueError("oracle policy identity mismatch")
        if self.model_digest!=driver.model_digest:raise ValueError("oracle model identity mismatch")
        if not self.policy_digest:
            self.policy_digest=actual_policy;self.identity=stable_digest({"source":self.source_digest,"profile":self.profile,"policy":self.policy_digest,"model":self.model_digest})
        if profile is not None and profile!=self.profile:raise ValueError("oracle profile mismatch")
        if policy_digest is not None and self.policy_digest and policy_digest!=self.policy_digest:raise ValueError("oracle policy identity mismatch")
        if self.driver is not None and self.driver is not driver:raise ValueError("oracle run identity mismatch")
        self.driver=driver;self.kernel.ops=driver.ops
    def _solve(self):
        remaining=max(0,self.max_work-self.kernel.work)
        if self.driver is not None:remaining=min(remaining,max(0,self.driver.max_work-self.driver.work))
        before=self.kernel.work
        try:closed=self.kernel.solve(remaining)
        finally:
            if self.driver is not None:self.driver.charge(self.kernel.work-before,"oracle_work")
        if not closed and self.driver is not None and self.driver.work>=self.driver.max_work:
            from pythonstan.analysis.engines.abstract.driver import BudgetStop
            raise BudgetStop("oracle_global_work")
    def publish_cell(self,cell,value,origin="ai"):
        changed=self.kernel.add_fact(("cell",cell),value,origin);self._solve();return changed
    def publish_field(self,objid,key,value,origin="ai"):
        changed=self.kernel.add_fact(("field",objid,key),value,origin);self._solve();return changed
    def register_copy(self,source,target):self.kernel.add_edge(("cell",source),("cell",target));self._solve()
    def register_load(self,base,key,target):self.kernel.add_load(("cell",base),key,("cell",target));self._solve()
    def register_store(self,base,key,source):self.kernel.add_store(("cell",base),key,("cell",source));self._solve()
    def candidates(self,cell,consumer=None):
        if consumer and self.driver:
            key=(cell,consumer)
            if key not in self._subscriptions:
                self._subscriptions.add(key)
                self.kernel.subscribe(("cell",cell),lambda node,version,c=consumer:self.driver.enqueue(c,"oracle_fact_growth") if c in self.driver.units else None)
        self._solve();value=self.kernel.value(("cell",cell));self.actual_consumptions+=1
        if not value.is_bottom():self.nonempty_consumptions+=1
        return CandidateAnswer(value,"partial",self.kernel.version,tuple(sorted(self.kernel.origins[("cell",cell)])),self.identity)
    def observe_call(self,caller,callee,site):self.calls.add((caller,callee,site))
    def statistics(self):return {**self.kernel.statistics(),"candidate_reads":self.actual_consumptions,"nonempty_candidate_reads":self.nonempty_consumptions,"call_facts":len(self.calls),"coverage":"partial","permits_exclusion":False}
