"""Independent interprocedural equation solver over source-bound Python AST IR.

A unit is a module initializer or finite-context function equation. Calls grow a
shared entry store and subscribe their caller; changes to exit summaries enqueue
all consumers. Recursive calls read the current equation approximation, never a
depth-cap substitute. No PTA, World singleton, target import or target execution
is used by this engine.
"""
from __future__ import annotations
from collections import deque, defaultdict
from dataclasses import replace
import ast
import hashlib
import time
from typing import Mapping
from pythonstan.analysis.contracts import SourceModule, EntryScenario, EngineSnapshot,stable_digest
from pythonstan.analysis.domains.values import Value, ValueOperations, ValuePolicy
from .state import Store, HeapObject, Frame, Flow, FunctionInfo, Unit

class BudgetStop(Exception): pass

class InterproceduralDriver:
    def __init__(self, modules, options=None, oracle=None, observer=None,program=None):
        self.options=dict(options or {});self.abstract=dict(self.options.get("abstract",{}));self.abstract.update(self.abstract.get("solver_options",{}));self.resources=dict(self.options.get("resources",{}))
        from pythonstan.frontend.source import parse_sources
        if program is not None:
            expected={}
            for item in modules.values() if isinstance(modules,Mapping) else modules:
                source=item if isinstance(item,SourceModule) else SourceModule(item["module"],item.get("filename",item["module"]+".py"),item.get("source",item.get("code","")),item.get("digest",""),item.get("is_package",False),item.get("original_bytes_digest"),item.get("encoding","utf-8"),item.get("import_root_digest",""))
                if source.module in expected:raise ValueError("duplicate source module identity")
                expected[source.module]=source
            if expected!=dict(program.modules):raise ValueError("pre-parsed SourceProgram input identity mismatch")
        program=parse_sources(modules) if program is None else program
        self.modules=program.modules;self.trees=program.trees;self.nodes=program.nodes;self.node_modules=program.node_modules
        self.source_digest=stable_digest({name:source.digest for name,source in sorted(self.modules.items())})
        self.policy_digest=stable_digest(self.options);self.model_digest="pythonstan-builtins-v1"
        self.future_annotations=frozenset(module for module,tree in self.trees.items() if any(isinstance(node,ast.ImportFrom) and node.module=="__future__" and any(alias.name=="annotations" for alias in node.names) for node in tree.body))
        self.ops=ValueOperations(ValuePolicy(max_const_strings=self.abstract.get("max_const_strings",10),max_const_ints=self.abstract.get("int_domain_max_consts",10),max_addr_set_size=self.abstract.get("max_addr_set_size",50),max_scalar_pairs=self.abstract.get("max_scalar_pairs",256)))
        self.oracle=oracle;self.observer=observer
        self.units={};self.unit_scenarios={};self.functions={};self.queue=deque();self.queued=set();self.current=None;self.scenario=""
        self.reads=set();self.writes=set();self.calls=set();self.exceptions=set();self.obligations=set();self.events=[]
        self.work=0;self.started=0;self.stats=defaultdict(int);self.root_keys=[];self.phase_trace=[];self.solver_status="converged"
        self.max_work=int(self.resources.get("max_work",self.abstract.get("max_iterations",100000)))
        self.max_seconds=float(self.resources.get("max_seconds",0) or 0)
        self.context_depth=int(self.abstract.get("context_depth",2))
        self.max_paths=max(1,int(self.abstract.get("max_traces_per_block",8)))
        self.region_engine=None;self.region_plan=None;self.region_scheduler=None;self.summary_provider=None
        from .transfer import Transfer
        self.transfer=Transfer(self)

    def point(self,node):return self.nodes.get(id(node),f"generated:{type(node).__name__}")
    def charge(self,amount,category):
        if amount<0:raise ValueError("negative work charge")
        if amount>self.max_work-self.work:raise BudgetStop("max_work")
        self.work+=amount;self.stats[category]+=amount
    def tick(self,node=None):
        self.charge(1,"ast_work")
        if self.max_seconds and time.monotonic()-self.started>self.max_seconds:raise BudgetStop("max_seconds")
        if node is not None:self.emit("transfer",point=self.point(node),unit=self.current,operation=type(node).__name__)
    def emit(self,kind,**data):
        event={"schema_version":1,"kind":kind,**data}
        self.stats["observation_events"]+=1
        if self.observer is not None:
            try:
                if hasattr(self.observer,"emit"):self.observer.emit(event)
                elif callable(self.observer):self.observer(event)
            except Exception:self.stats["observation_errors"]+=1
    def enqueue(self,key,reason):
        if key not in self.queued:self.queue.append(key);self.queued.add(key);self.emit("work_enqueue",unit=key,reason=reason)
    def open(self,reason,store=None,node=None):
        self.obligations.add(reason)
        if store is not None:store.obligations|=frozenset((reason,))
        self.emit("open_obligation",reason=reason,point=self.point(node) if node else None)
    def register_module(self,module,store):
        identifier=f"module:{module}"
        if identifier not in store.heap:store.heap[identifier]=HeapObject("module",metadata={"module":module})
        return Value.ref(identifier)
    def module_key(self,module):return f"{self.scenario}:module:{module}"
    def request_module(self,module,store,caller=None,site=None):
        if module not in self.modules:
            return self.transfer.unknown(store,site,"external_module:"+module)
        result=store.copy();ref=self.register_module(module,result)
        status=result.initialized.get(module,"maybe" if result.havoc_imports else "unseen")
        if status in ("initialized","initializing"):
            return [Flow(result,value=ref)]
        key=self.module_key(module)
        entry=result.clear_effects();entry.initialized[module]="initializing"
        for name,descriptor in self.options.get("inputs",{}).get("bindings",{}).items():
            prefix=module+"."
            if name.startswith(prefix):entry.cells[f"global:{module}:{name[len(prefix):]}"]=self.input_value(descriptor)
        frame=Frame(module,module,(),key,is_module=True)
        self._request(key,frame,tuple(self.trees[module].body),entry,True,caller)
        flows=[Flow(result.copy(),value=self.ops.join(ref,Value.unknown()) if result.havoc_imports else ref)] if status in {"maybe","partial"} else []
        for outcome in self.units[key].outcomes:
            applied=result.apply(outcome,self.ops)
            if outcome.kind in ("normal","return"):
                applied.initialized[module]="initialized";flows.append(Flow(applied,value=ref))
            else:flows.append(Flow(applied,outcome.kind,outcome.value,outcome.exception))
        return flows

    def _request(self,key,frame,body,entry,is_module,caller):
        if key not in self.units:
            self.units[key]=Unit(key,frame,body,entry,is_module=is_module);self.unit_scenarios[key]=self.scenario;self.enqueue(key,"new_entry")
        else:
            unit=self.units[key]
            if not entry.leq(unit.entry,self.ops):
                expanded=unit.entry.join(entry,self.ops,widen=unit.visits>int(self.abstract.get("widen_delay",2)))
                unit.entry=expanded;self.enqueue(key,"entry_growth");self.stats["entry_growth"]+=1
        if caller and caller!=key:self.units[key].consumers.add(caller)
        elif caller==key:self.units[key].consumers.add(caller)
        if caller:self.emit("call_dependency",caller=caller,callee=key,revision=self.units[key].revision)

    def request_function(self,identifier,args,keywords,store,caller_frame,site):
        info=self.functions[identifier]
        from pythonstan.analysis.semantics.binding import bind_arguments
        function_object=store.heap.get(identifier)
        defaults=info.defaults;kw_defaults=info.kw_defaults
        if function_object is not None and function_object.havoc:
            return self.transfer.unknown(store,site,"havoc_function_metadata")
        if function_object is not None:
            defaults=tuple(function_object.fields.get(f"$default:{i}",value) for i,value in enumerate(info.defaults))
            kw_defaults=tuple((key,function_object.fields.get("$kwdefault:"+key,value)) for key,value in info.kw_defaults)
            if function_object.havoc:
                defaults=tuple(self.ops.join(value,Value.unknown()) for value in defaults)
                kw_defaults=tuple((key,self.ops.join(value,Value.unknown())) for key,value in kw_defaults)
        bound=bind_arguments(info.node.args,args,keywords,defaults,kw_defaults)
        if bound.errors:
            self.exceptions.add("TypeError")
            return [Flow(store.copy(),"raise",Value.constant("TypeError"),"TypeError")]
        if self.summary_provider is not None:
            try:application=self.summary_provider.try_apply(self,identifier,args,keywords,store.copy(),caller_frame,site)
            except BudgetStop:raise
            except Exception:
                application=None;self.open("summary_provider_failure",store,site)
            if application is not None:
                self.calls.add((caller_frame.qualname,info.qualname,self.point(site)))
                self.stats["checked_summary_applications"]+=1
                self.emit("checked_summary_application",callee=info.qualname,point=self.point(site))
                return application
        context=(*caller_frame.context,self.point(site))[-self.context_depth:] if self.context_depth else ()
        heap_partition=self.semantic_input_partition(info,args,keywords,defaults,store)
        key=f"{self.scenario}:call:{identifier}@{'|'.join(context)}:heap={heap_partition}"
        closure=tuple(function_object.metadata.get("closure",info.closure)) if function_object is not None else info.closure
        frame=Frame(info.module,info.qualname,context,key,info.locals,closure,info.globals,info.nonlocals)
        entry=store.clear_effects()
        for name in frame.locals:entry.cells[frame.cell(name)]=Value(unbound=True)
        for name,value in bound.bindings:entry.cells[frame.cell(name)]=value
        if info.node.args.vararg:
            objid=f"varargs:{key}";entry.heap[objid]=HeapObject("tuple",{i:v for i,v in enumerate(bound.varargs)},metadata={"length":len(bound.varargs)})
            entry.cells[frame.cell(info.node.args.vararg.arg)]=Value.ref(objid)
        if info.node.args.kwarg:
            objid=f"kwargs:{key}";entry.heap[objid]=HeapObject("dict",dict(bound.kwargs),metadata={"length":len(bound.kwargs)})
            entry.cells[frame.cell(info.node.args.kwarg.arg)]=Value.ref(objid)
        if info.is_generator or info.is_async:
            objid=f"{'coroutine' if info.is_async else 'generator'}:{key}:{self.point(site)}"
            result=store.copy();result.heap[objid]=HeapObject("coroutine" if info.is_async else "generator",metadata={"function":identifier,"frame":frame,"entry":entry,"body":tuple(info.node.body),"stage":"created","index":0})
            return [Flow(result,value=Value.ref(objid))]
        body=(ast.Return(info.node.body),) if isinstance(info.node,ast.Lambda) else tuple(info.node.body)
        self._request(key,frame,body,entry,False,self.current)
        self.calls.add((caller_frame.qualname,info.qualname,self.point(site)))
        self.emit("call",caller=caller_frame.qualname,callee=info.qualname,point=self.point(site),context=context)
        if self.oracle is not None:
            try:
                if hasattr(self.oracle,"observe_call"):self.oracle.observe_call(caller_frame.qualname,info.qualname,self.point(site))
            except Exception:self.open("oracle_observation_failure",store,site)
        result=[]
        for outcome in self.units[key].outcomes:
            applied=store.apply(outcome,self.ops)
            kind="normal" if outcome.kind=="return" else outcome.kind
            result.append(Flow(applied,kind,outcome.value,outcome.exception))
        return result

    def semantic_input_partition(self,info,args,keywords,defaults,store):
        """Finite observable heap-state inputs distinguish reusable equations.

        Exact iterator positions/list lengths 0..31 and finite stages form a
        source-independent finite alphabet. Larger/changing states share a
        coarse equation whose metadata join is explicit havoc, never a stale
        exact representative. No full caller/context is nested in the token.
        """
        values=[*args,*[value for _,value in keywords],*defaults]
        function_object=store.heap.get(info.identifier)
        closure=tuple(function_object.metadata.get("closure",info.closure)) if function_object else info.closure
        values.extend(store.cells.get(cell,Value.bottom()) for _,cell in closure)
        names={node.id for node in ast.walk(info.node) if isinstance(node,ast.Name) and isinstance(node.ctx,ast.Load)}-set(info.locals)
        values.extend(store.cells.get(f"global:{info.module}:{name}",Value.bottom()) for name in names)
        pending=[ref for value in values for ref in value.refs];seen=set();tokens=[]
        while pending and len(seen)<64:
            ref=pending.pop()
            if ref in seen:continue
            seen.add(ref);obj=store.heap.get(ref)
            if obj is None:continue
            for name in ("index","length","stage"):
                if name not in obj.metadata:continue
                value=obj.metadata[name]
                if type(value) is int:value=value if 0<=value<32 else "many"
                elif type(value) is not str:value="abstract"
                tokens.append((ref.split("@",1)[0],name,value))
            for value in (*obj.fields.values(),obj.default):pending.extend(value.refs)
            container=obj.metadata.get("container")
            if isinstance(container,Value):pending.extend(container.refs)
        if pending:tokens.append(("reachable","overflow","many"))
        return stable_digest(sorted(tokens,key=str))[:16]

    def compact(self,flows):
        # Subsumption removes repeated recursive approximations before counting
        # paths; otherwise duplicate return paths can oscillate below the cap.
        maximal=[]
        for flow in flows:
            same=lambda other:(flow.kind,flow.exception,flow.continuation,flow.cleanups)==(other.kind,other.exception,other.continuation,other.cleanups)
            if any(same(old) and self.ops.leq(flow.value,old.value) and flow.store.leq(old.store,self.ops) for old in maximal):continue
            maximal=[old for old in maximal if not (same(old) and self.ops.leq(old.value,flow.value) and old.store.leq(flow.store,self.ops))]
            maximal.append(flow)
        flows=maximal
        if len(flows)<=self.max_paths:return flows
        buckets={}
        for flow in flows:
            key=(flow.kind,flow.exception,flow.continuation,flow.cleanups)
            if key not in buckets:buckets[key]=flow
            else:
                old=buckets[key];buckets[key]=replace(flow,store=old.store.join(flow.store,self.ops),value=self.ops.join(old.value,flow.value))
        self.stats["trace_merges"]+=len(flows)-len(buckets)
        return list(buckets.values())

    def _outcomes_equal(self,left,right):
        if len(left)!=len(right):return False
        for a,b in zip(sorted(left,key=lambda f:(f.kind,f.exception or "")),sorted(right,key=lambda f:(f.kind,f.exception or ""))):
            if a.kind!=b.kind or a.exception!=b.exception or not self.ops.leq(a.value,b.value) or not self.ops.leq(b.value,a.value) or not a.store.leq(b.store,self.ops) or not b.store.leq(a.store,self.ops):return False
        return True

    def run(self,entries=None):
        self.started=time.monotonic()
        if self.region_scheduler is not None:self.region_scheduler.validate(self)
        if self.oracle is not None:
            self.phase_trace.append(dict(getattr(self.oracle,"phase_record",{"engine":"andersen","phase":"incremental_candidate_oracle","whole_program_prepass":False,"coverage":"partial"})))
        self.phase_trace.append({"engine":"abstract","phase":"interprocedural_fixed_point","pta_started":False})
        entries=tuple((EntryScenario(m) for m in self.modules) if entries is None else entries)
        if not entries:
            return EngineSnapshot(engine="ai_primary",solver_status="failed",coverage_status="unsupported",obligations=("no_entry",),phase_trace=tuple(self.phase_trace))
        query_modules={query.get("module") for query in self.options.get("queries",()) if isinstance(query,Mapping) and query.get("module")}
        # Query demand changes scheduling, never the declared root set.
        entries=tuple(sorted(enumerate(entries),key=lambda pair:(getattr(pair[1],"root_module",None) not in query_modules,pair[0])))
        try:
            if self.oracle is not None and hasattr(self.oracle,"bind"):self.oracle.bind(self)
            if self.summary_provider is not None and hasattr(self.summary_provider,"charge_setup"):self.summary_provider.charge_setup(self)
            for index,entry in entries:
                module=getattr(entry,"root_module",getattr(entry,"module",None)) if not isinstance(entry,Mapping) else entry.get("root_module",entry.get("module"))
                qualname=getattr(entry,"entry_qualname",getattr(entry,"qualname",None)) if not isinstance(entry,Mapping) else entry.get("entry_qualname",entry.get("qualname"))
                self.scenario=getattr(entry,"scenario_id","") or f"root-{index}-{module}"
                key=self.module_key(module);self.root_keys.append((self.scenario,module,key,qualname,entry))
                self.request_module(module,Store())
            # All scenarios are pending before solving; one initializer cannot
            # silently prevent the remaining declared roots from being queued.
            self._solve()
            for scenario,module,key,qualname,entry in self.root_keys:
                if qualname:
                    self.scenario=scenario;self._activate_entry(module,qualname,entry)
            self._solve()
        except BudgetStop as exc:
            self.solver_status="budget_exhausted";self.open("budget:"+str(exc));self.stats["pending_units"]=len(self.queue)+1
        elapsed=time.monotonic()-self.started
        return self.snapshot(elapsed)

    def _activate_entry(self,module,qualname,entry):
        key=self.module_key(module);unit=self.units.get(key)
        if unit is None:return
        short=qualname.split('.')[-1]
        arguments=getattr(entry,"arguments",()) if not isinstance(entry,Mapping) else entry.get("arguments",())
        for out in unit.outcomes:
            if out.kind not in ("normal","return"):continue
            callee=out.store.cells.get(f"global:{module}:{short}",Value.bottom())
            args=[];keywords=[]
            if arguments:
                args=[self.input_value(x) for x in arguments if not (isinstance(x,Mapping) and "keyword" in x)]
                keywords=[(x["keyword"],self.input_value(x.get("abstract_value",{"top":True}))) for x in arguments if isinstance(x,Mapping) and "keyword" in x]
            for identifier in callee.refs:
                info=self.functions.get(identifier)
                if info:
                    if not arguments:
                        args=[Value.unknown() for _ in (*info.node.args.posonlyargs,*info.node.args.args)]
                        keywords=[(x.arg,Value.unknown()) for x in info.node.args.kwonlyargs if x.arg not in dict(info.kw_defaults)]
                    fake=info.node
                    self.request_function(identifier,args,keywords,out.store,unit.frame,fake)
                else:self.open("entry_not_callable:"+qualname)
            if callee.is_bottom():self.open("entry_not_found:"+qualname)

    def _solve(self):
        while self.queue:
            key=self.queue.popleft();self.queued.discard(key);unit=self.units[key];self.current=key
            self.scenario=self.unit_scenarios[key];unit.visits+=1;self.stats["unit_evaluations"]+=1
            if unit.visits>int(self.abstract.get("max_iterations",100)):raise BudgetStop("unit_iterations")
            self.emit("work_dequeue",unit=key,revision=unit.revision)
            entry=unit.entry.clear_effects()
            flows=self.transfer.block(unit.body,[Flow(entry)],unit.frame)
            normalized=[]
            for flow in flows:
                if not unit.is_module and flow.kind=="normal":flow=Flow(flow.store,"return",Value.constant(None))
                if unit.is_module and flow.kind=="normal":flow.store.initialized[unit.frame.module]="initialized"
                normalized.append(flow)
            normalized=self.compact(normalized)
            if not self._outcomes_equal(unit.outcomes,normalized):
                unit.outcomes=normalized;unit.revision+=1
                if self.observer is not None:self.emit("summary_update",unit=key,qualname=unit.frame.qualname,revision=unit.revision,outcomes=[{"kind":f.kind,"value":f.value.export(),"exception":f.exception} for f in normalized],consumers=sorted(unit.consumers))
                else:self.emit("summary_update",unit=key,revision=unit.revision,outcomes=[f.kind for f in normalized])
                for consumer in unit.consumers:self.enqueue(consumer,"callee_exit_growth")
            self.current=None

    def input_value(self,descriptor):
        if isinstance(descriptor,Value):return descriptor
        if isinstance(descriptor,Mapping):
            sort=descriptor.get("type")
            if "constants" in descriptor:return self.ops.join_all(Value.constant(x) for x in descriptor["constants"])
            if "value" in descriptor:return Value.constant(descriptor["value"])
            if sort=="str":return Value(strings=self.ops.string.top())
            if sort=="int":return Value(ints=self.ops.integer.top())
            if sort=="bool":return Value(bools=frozenset((False,True)))
            if sort=="none":return Value.constant(None)
            return Value.unknown()
        return Value.constant(descriptor)

    def snapshot(self,elapsed):
        values={};returns={};outcomes={};kinds={};heaps={};module_heaps={};procedure_heaps={};module_exits={};procedure_meta={}
        def merge_heap(target,store):
            for objid,obj in store.heap.items():target[objid]=target[objid].join(obj,self.ops) if objid in target else obj.copy()
        for unit in self.units.values():
            if not unit.is_module:
                returns.setdefault(unit.frame.qualname,Value.bottom())
                procedure_meta.setdefault(unit.frame.qualname,{"normal_exit":False,"exceptions":set(),"suspends":False})
            for flow in unit.outcomes:
                kinds.update((k,v.kind) for k,v in flow.store.heap.items())
                merge_heap(heaps,flow.store)
                if unit.is_module and flow.kind in {"normal","return"}:merge_heap(module_heaps.setdefault(unit.frame.module,{}),flow.store)
                elif not unit.is_module and flow.kind=="return":merge_heap(procedure_heaps.setdefault(unit.frame.qualname,{}),flow.store)
                outcomes.setdefault(unit.frame.qualname,[]).append({"kind":flow.kind,"value":flow.value.export(kinds),"exception":flow.exception,"context":list(unit.frame.context),"revision":unit.revision})
                if unit.is_module:
                    module_exits.setdefault(unit.frame.module,[]).append({"scenario":self.unit_scenarios[unit.key],"kind":flow.kind,"exception":flow.exception})
                if unit.is_module and flow.kind in ("normal","return"):
                    target=values.setdefault(unit.frame.module,{})
                    prefix=f"global:{unit.frame.module}:"
                    for cell,value in flow.store.cells.items():
                        if cell.startswith(prefix):
                            symbol=cell[len(prefix):];old=target.get(symbol,Value.bottom());target[symbol]=self.ops.join(old,value)
                elif not unit.is_module:
                    meta=procedure_meta[unit.frame.qualname]
                    if flow.kind=="return":returns[unit.frame.qualname]=self.ops.join(returns[unit.frame.qualname],flow.value);meta["normal_exit"]=True
                    if flow.kind=="raise":meta["exceptions"].add(flow.exception)
                    if flow.kind=="suspend":meta["suspends"]=True
        for query in self.options.get("queries",()):
            if not isinstance(query,Mapping) or query.get("kind")!="value" or query.get("point","module_exit")!="module_exit":continue
            module=query.get("module");symbol=query.get("symbol")
            if not module or not symbol:continue
            for unit in self.units.values():
                if not unit.is_module or unit.frame.module!=module:continue
                for flow in unit.outcomes:
                    if flow.kind not in {"normal","return"} or module not in flow.store.havoc_namespaces:continue
                    bound=flow.store.effective_cell(f"global:{module}:{symbol}")
                    values.setdefault(module,{})[symbol]=self.ops.join(values.get(module,{}).get(symbol,Value.bottom()),bound)
        for obj in heaps.values():
            if obj.kind in ("generator","coroutine"):
                info=self.functions[obj.metadata["function"]]
                returns.setdefault(info.qualname,Value.bottom())
                procedure_meta[info.qualname]={"normal_exit":False,"exceptions":set(),"suspends":info.is_generator,"body_executed_at_creation":False}
                outcomes.setdefault(info.qualname,[])
        def export(value,seen=frozenset(),heap_view=None):
            heap_view=heaps if heap_view is None else heap_view
            out=value.export(kinds)
            shapes=[]
            for ref in value.refs:
                if ref in seen:continue
                obj=heap_view.get(ref)
                if obj and obj.kind in ("list","tuple","range") and "length" in obj.metadata and obj.default.is_bottom() and not obj.havoc:
                    items=[];exact=True
                    for index in range(obj.metadata["length"]):
                        item=obj.fields.get(index,Value.bottom());constants=item.exact_constants()
                        if constants is None or len(constants)!=1:exact=False;break
                        items.append(constants[0])
                    if exact:shapes.append(items)
                if obj and obj.kind=="dict":out.setdefault("slots",[]).append({"object":ref,"entries":[{"key":key,"value":export(item,seen|{ref},heap_view)} for key,item in obj.fields.items() if isinstance(key,(str,int,bool,float)) or key is None],"default":obj.default.export(kinds),"havoc":obj.havoc})
            if shapes and all(shape==shapes[0] for shape in shapes):out["items"]=shapes[0]
            return out
        return_rows={}
        for name,value in returns.items():
            row=export(value,heap_view=procedure_heaps.get(name,heaps));meta=procedure_meta.get(name,{})
            row.update({key:sorted(v) if isinstance(v,set) else v for key,v in meta.items()});return_rows[name]=row
        self.stats.update({"work":self.work,"total_work":self.work,"units":len(self.units),"analyzed_units":sum(unit.visits>0 for unit in self.units.values()),"root_scenarios":len(self.root_keys),"initialized_roots":sum(any(flow.kind in {"normal","return"} for flow in self.units[key].outcomes) for _,_,key,_,_ in self.root_keys if key in self.units),"summaries":sum(bool(u.outcomes) for u in self.units.values()),"functions":len(self.functions),"pending_units":max(self.stats.get("pending_units",0),len(self.queue)),"elapsed_seconds":elapsed,"pta_work":0})
        if self.oracle is not None and hasattr(self.oracle,"statistics"):
            self.stats["oracle"]=self.oracle.statistics();self.stats["pta_work"]=self.stats["oracle"].get("work",0)
        return EngineSnapshot(**{"schema_version":1,"engine":"hybrid" if self.oracle is not None else "ai_primary","solver_status":self.solver_status,"coverage_status":"open" if self.obligations else "closed_under_profile","profile":self.options.get("semantic_profile","python-core-v1"),"values":{m:{n:export(v,heap_view=module_heaps.get(m,heaps)) for n,v in rows.items()} for m,rows in values.items()},"module_exits":module_exits,"returns":return_rows,"outcomes":outcomes,"calls":[{"caller":a,"callee":b,"site":s} for a,b,s in sorted(self.calls)],"effects":{"reads":[list(x) for x in sorted(self.reads,key=str)],"writes":[list(x) for x in sorted(self.writes,key=str)],"calls":sorted({b for _,b,_ in self.calls}),"exceptions":sorted(self.exceptions),"prefix_events":[list(x) for x in self.events],"havoc":[list(x) for x in self.writes if x[0]=="unknown"]},"obligations":[{"code":x,"status":"open"} for x in sorted(self.obligations)],"stats":dict(self.stats),"phase_trace":self.phase_trace})
