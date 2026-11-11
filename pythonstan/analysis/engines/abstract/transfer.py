"""Python semantic operations on explicit stores and completion alternatives.

Only builtin scalar operators are evaluated by the host. Source functions,
imports, descriptors and cleanup run through the abstract driver and store.
"""
from __future__ import annotations
from dataclasses import replace
import ast
import operator
from itertools import product
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.domains.relations import PartitionToken,KeyValueRelation
from pythonstan.analysis.semantics.names import classify_bindings
from pythonstan.analysis.semantics.control import resume_pending
from .state import Store,HeapObject,Frame,Flow,FunctionInfo

from pythonstan.models.builtins import ABSTRACT_BUILTINS as _BUILTINS
from pythonstan.models.builtin_attributes import known_builtin_attribute
from pythonstan.models.builtins import PYTHON312_BUILTIN_NAMES
from pythonstan.models.exceptions import EXCEPTION_NAMES,builtin_exception_ancestors,canonical_builtin_exception_name
_BUILTINS = _BUILTINS | PYTHON312_BUILTIN_NAMES

class Transfer:
    def __init__(self,driver):self.d=driver;self.ops=driver.ops
    def normal(self,store,value=None):return Flow(store,value=Value.constant(None) if value is None else value)
    def error(self,store,name):
        self.d.exceptions.add(name);return Flow(store,"raise",Value.constant(name),name)
    def unknown(self,store,node,reason,*,havoc=True):
        result=store.copy();self.d.open(reason,result,node)
        if havoc:
            for objid,obj in result.heap.items():obj.havoc=True;result.writes.add(("heap",objid,None))
            captured=set()
            for obj in result.heap.values():
                captured.update(cell for _,cell in obj.metadata.get("closure",()))
                suspended_frame=obj.metadata.get("frame")
                if suspended_frame is not None:captured.update(cell for _,cell in suspended_frame.closure)
            for cell in list(result.cells):
                if cell.startswith("global:") or cell in captured or "local:" not in cell:result.write_cell(cell,Value(top=True,unbound=True))
            result.havoc_namespaces|=frozenset(self.d.modules)
            result.initialized={module:"maybe" for module in result.initialized};result.havoc_imports=True
            self.d.writes.add(("unknown","reachable_heap_and_globals",self.d.point(node)))
            self.d.calls.add(("<unknown>","<callback>",self.d.point(node)))
        self.d.exceptions.add("BaseException")
        return [Flow(result,value=Value.unknown()),Flow(result.copy(),"raise",Value.unknown(),"AnyException")]
    def block(self,body,flows,frame):
        for position,node in enumerate(body):
            result=[]
            for flow in flows:
                if flow.kind=="normal":
                    for produced in (self.d.region_scheduler.execute(node,flow,frame,self.d) if self.d.region_scheduler is not None else self.statement(node,flow.store,frame)):
                        if produced.kind=="suspend":produced=replace(produced,continuation=(*produced.continuation,*body[position+1:]))
                        result.append(produced)
                else:result.append(flow)
            flows=self.d.compact(result)
            if not flows:break
        return flows
    def read_name(self,name,store,frame,node):
        cell=frame.cell(name);self.d.reads.add((frame.qualname,name,self.d.point(node)))
        value=store.effective_cell(cell)
        if self.d.oracle is not None and hasattr(self.d.oracle,"candidates") and (value.top or value.unbound or value.is_bottom()):
            candidate=self.d.oracle.candidates(cell,self.d.current)
            if not candidate.values.is_bottom():
                value=self.ops.join(value,candidate.values)
                self.d.stats["oracle_values_consumed"]+=1
        if value.unbound and name in ("__name__","__package__","__file__") and (frame.is_module or name not in frame.locals and name not in dict(frame.closure) and name not in frame.nonlocals):
            source=self.d.modules[frame.module];value=self.ops.join(value.without_unbound(),Value.constant(frame.module if name=="__name__" else source.filename if name=="__file__" else frame.module if source.is_package else frame.module.rpartition('.')[0]))
        elif value.unbound and name in _BUILTINS and (frame.is_module or name not in frame.locals and name not in dict(frame.closure) and name not in frame.nonlocals):
            value=self.ops.join(value.without_unbound(),Value.ref("builtin:"+name))
        if self.d.observer is not None:self.d.emit("name_read",point=self.d.point(node),unit=self.d.current,cell=cell,value=value.export())
        out=[]
        if not value.without_unbound().is_bottom():out.append(Flow(store.copy(),value=value.without_unbound()))
        if value.unbound:out.append(self.error(store.copy(),"UnboundLocalError" if name in frame.locals else "NameError"))
        return out
    def write_name(self,name,value,store,frame,node):
        cell=frame.cell(name);store.write_cell(cell,value)
        if self.d.oracle is not None and hasattr(self.d.oracle,"publish_cell"):self.d.oracle.publish_cell(cell,value,"ai_name_write")
        self.d.writes.add((frame.qualname,name,self.d.point(node)))
        if self.d.observer is not None:self.d.emit("name_write",point=self.d.point(node),unit=self.d.current,cell=cell,value=value.export())
    def alloc(self,node,store,frame,kind,fields=None,metadata=None,class_ids=()):
        objid=f"{kind}:{self.d.point(node)}@{'|'.join(frame.context)}"
        old=store.heap.get(objid)
        obj=HeapObject(kind,fields or {},singleton=old is None,class_ids=frozenset(class_ids),metadata=metadata or {})
        if old is not None:obj=old.join(obj,self.ops);obj.singleton=False
        store.heap[objid]=obj
        if self.d.oracle is not None and hasattr(self.d.oracle,"publish_field"):
            for key,value in obj.fields.items():self.d.oracle.publish_field(objid,key,value,"ai_allocation")
        self.d.stats["allocations"]+=1
        return Value.ref(objid)
    def eval_nodes(self,nodes,store,frame):
        states=[(store,())];errors=[]
        for node in nodes:
            next_states=[]
            for current,values in states:
                for flow in self.expr(node,current,frame):
                    if flow.kind=="normal":next_states.append((flow.store,(*values,flow.value)))
                    else:errors.append(flow)
            states=next_states
        return states,errors
    def expr(self,node,store,frame):
        self.d.tick(node)
        if node is None:return [self.normal(store.copy())]
        if isinstance(node,ast.Constant):return [Flow(store.copy(),value=Value.constant(node.value))]
        if isinstance(node,ast.Name):return self.read_name(node.id,store,frame,node)
        if isinstance(node,ast.NamedExpr):
            result=[]
            for flow in self.expr(node.value,store,frame):
                if flow.kind=="normal":self.assign(node.target,flow.value,flow.store,frame,node)
                result.append(flow)
            return result
        if isinstance(node,ast.Lambda):return self.define_function(node,store,frame,"<lambda>")
        if isinstance(node,(ast.List,ast.Tuple,ast.Set)):
            states,errors=self.eval_nodes(node.elts,store,frame);kind=type(node).__name__.lower()
            return errors+[Flow(s,value=self.alloc(node,s,frame,kind,{i:v for i,v in enumerate(vals)},{"length":len(vals)})) for s,vals in states]
        if isinstance(node,ast.Dict):
            states=[(store.copy(),{})];errors=[]
            for key,value in zip(node.keys,node.values):
                next_states=[]
                for s,fields in states:
                    if key is None:
                        for flow in self.expr(value,s,frame):
                            if flow.kind!="normal":errors.append(flow);continue
                            merged=dict(fields)
                            for ref in flow.value.refs:
                                obj=flow.store.heap.get(ref)
                                if obj and obj.kind=="dict":merged.update(obj.fields)
                                else:self.d.open("dynamic_dict_unpack",flow.store,node)
                            next_states.append((flow.store,merged))
                    else:
                        pairs,ex=self.eval_nodes((key,value),s,frame);errors.extend(ex)
                        for s2,(k,v) in pairs:
                            merged=dict(fields);constants=k.exact_constants()
                            if constants is None:merged["<unknown-key>"]=self.ops.join(merged.get("<unknown-key>",Value.bottom()),v)
                            else:
                                for concrete in constants:
                                    try:hash(concrete);merged[concrete]=v
                                    except TypeError:errors.append(self.error(s2.copy(),"TypeError"))
                            next_states.append((s2,merged))
                states=next_states
            result=[]
            for s,fields in states:
                unknown=fields.pop("<unknown-key>",Value.bottom());ref=self.alloc(node,s,frame,"dict",fields,{"length":len(fields)})
                for r in ref.refs:s.heap[r].default=unknown
                result.append(Flow(s,value=ref))
            return errors+result
        if isinstance(node,ast.Attribute):
            result=[]
            for flow in self.expr(node.value,store,frame):
                if flow.kind!="normal":result.append(flow)
                else:result.extend(self.getattr(flow.value,node.attr,flow.store,frame,node))
            return result
        if isinstance(node,ast.Subscript):
            states,errors=self.eval_nodes((node.value,node.slice),store,frame);result=list(errors)
            for s,(base,key) in states:result.extend(self.getitem(base,key,s,frame,node))
            return result
        if isinstance(node,ast.Slice):
            states,errors=self.eval_nodes(tuple(x for x in (node.lower,node.upper,node.step) if x is not None),store,frame)
            return errors+[Flow(s,value=self.alloc(node,s,frame,"slice",{i:v for i,v in enumerate(vals)})) for s,vals in states]
        if isinstance(node,ast.Call):return self.call(node,store,frame)
        if isinstance(node,ast.BinOp):
            states,errors=self.eval_nodes((node.left,node.right),store,frame);result=list(errors)
            for s,(a,b) in states:
                val,exc=self.ops.scalar_binary(node.op,a,b)
                if not val.is_bottom():result.append(Flow(s.copy(),value=val))
                for name in exc:
                    if name.startswith(("unknown_","resource_")):
                        result.extend(self.unknown(s,node,name))
                    else:result.append(self.error(s.copy(),name))
            return result
        if isinstance(node,ast.UnaryOp):
            result=[]
            for flow in self.expr(node.operand,store,frame):
                if flow.kind!="normal":result.append(flow);continue
                if isinstance(node.op,ast.Not):
                    for tested in self.truth_flows(flow.value,flow.store,frame,node):
                        result.append(Flow(tested.store,value=Value(bools=frozenset(not x for x in tested.value.bools))) if tested.kind=="normal" else tested)
                    continue
                else:
                    if flow.value.refs or flow.value.top:
                        result.extend(self.unknown(flow.store,node,"unknown_unary_operator"));continue
                    constants=flow.value.exact_constants();func={ast.USub:operator.neg,ast.UAdd:operator.pos,ast.Invert:operator.invert}.get(type(node.op))
                    if constants is None or func is None:value=Value.unknown()
                    else:
                        values=[]
                        for c in constants:
                            try:values.append(Value.constant(func(c)))
                            except (TypeError,ValueError):result.append(self.error(flow.store.copy(),"TypeError"))
                        value=self.ops.join_all(values)
                if not value.is_bottom():result.append(Flow(flow.store,value=value))
            return result
        if isinstance(node,ast.Compare):
            states,errors=self.eval_nodes((node.left,*node.comparators),store,frame);result=list(errors)
            for s,vals in states:
                truth={True}
                for op,a,b in zip(node.ops,vals,vals[1:]):
                    if isinstance(op,(ast.Is,ast.IsNot)) and a.refs and b.refs and not (a.top or b.top):
                        equal=bool(a.refs&b.refs);different=bool(a.refs-b.refs or b.refs-a.refs) or any(not s.heap.get(r,HeapObject("object",singleton=False)).singleton for r in a.refs&b.refs)
                        booleans=({True} if equal else set())|({False} if different or not equal else set())
                        if isinstance(op,ast.IsNot):booleans={not x for x in booleans}
                        current=booleans
                    elif isinstance(op,(ast.In,ast.NotIn)) and b.refs:
                        current=set()
                        constants=a.exact_constants()
                        for r in b.refs:
                            obj=s.heap.get(r)
                            if obj and obj.kind=="dict" and constants is not None:
                                for c in constants:
                                    if c in obj.fields:current.add(True)
                                    if c not in obj.fields or c in obj.maybe_missing or not obj.default.is_bottom() or obj.havoc:current.add(False)
                            else:current.update((True,False))
                        if isinstance(op,ast.NotIn):current={not x for x in current}
                    else:
                        value,_=self.ops.scalar_binary(op,a,b);current=set(self.ops.truth(value))
                    truth={x and y for x in truth for y in current}
                result.append(Flow(s,value=Value(bools=frozenset(truth))))
            return result
        if isinstance(node,ast.BoolOp):
            flows=self.expr(node.values[0],store,frame)
            for operand in node.values[1:]:
                next_flows=[]
                for flow in flows:
                    if flow.kind!="normal":next_flows.append(flow);continue
                    for tested in self.truth_flows(flow.value,flow.store,frame,node):
                        if tested.kind!="normal":next_flows.append(tested);continue
                        proceed=isinstance(node.op,ast.And)
                        if (not proceed) in tested.value.bools:next_flows.append(Flow(tested.store.copy(),value=flow.value))
                        if proceed in tested.value.bools:next_flows.extend(self.expr(operand,tested.store.copy(),frame))
                flows=next_flows
            return flows
        if isinstance(node,ast.IfExp):
            result=[]
            for flow in self.expr(node.test,store,frame):
                if flow.kind!="normal":result.append(flow);continue
                for tested in self.truth_flows(flow.value,flow.store,frame,node):
                    if tested.kind!="normal":result.append(tested);continue
                    for truth,body in ((True,node.body),(False,node.orelse)):
                        if truth in tested.value.bools:
                            refined=self.refine_test(node.test,tested.store.copy(),frame,truth)
                            if refined is not None:result.extend(self.expr(body,refined,frame))
            return result
        if isinstance(node,ast.JoinedStr):
            states=[(store.copy(),"")];errors=[]
            for part in node.values:
                next_states=[]
                for s,text in states:
                    target=part.value if isinstance(part,ast.FormattedValue) else part
                    for flow in self.expr(target,s,frame):
                        if flow.kind!="normal":errors.append(flow);continue
                        constants=flow.value.exact_constants()
                        if constants is None:next_states.append((flow.store,None));continue
                        for c in constants:next_states.append((flow.store.copy(),None if text is None else text+str(c)))
                states=next_states
            return errors+[Flow(s,value=Value.constant(text) if text is not None else Value(strings=self.ops.string.top())) for s,text in states]
        if isinstance(node,(ast.Yield,ast.YieldFrom)):
            result=[]
            for flow in self.expr(node.value,store,frame):
                if flow.kind=="normal":result.append(Flow(flow.store,"suspend",flow.value))
                else:result.append(flow)
            return result
        if isinstance(node,ast.Await):
            result=[]
            for flow in self.expr(node.value,store,frame):
                if flow.kind!="normal":result.append(flow)
                else:result.extend(self.resume(flow.value,flow.store,frame,node,"await"))
            return result
        if isinstance(node,(ast.ListComp,ast.SetComp,ast.DictComp,ast.GeneratorExp)):
            return self.comprehension(node,store,frame)
        return self.unknown(store,node,"unsupported_expression:"+type(node).__name__)

    def truth(self,value,store):
        truths=set(self.ops.truth(replace(value,refs=frozenset())))
        for ref in value.refs:
            obj=store.heap.get(ref)
            if obj and obj.kind in ("list","tuple","dict","set","range") and "length" in obj.metadata and obj.default.is_bottom() and not obj.havoc:
                truths.add(bool(obj.metadata["length"]))
            else:truths.add(True)
        return frozenset(truths)

    def truth_flows(self,value,store,frame,node):
        """Execute Python truth protocols with their normal/exceptional effects."""
        result=[]
        known_sorts={"int","str","float","bool","none","bytes","complex","ellipsis","NotImplementedType"}
        simple=replace(value,refs=frozenset(),top=False,top_sorts=value.top_sorts&known_sorts)
        truths=self.ops.truth(simple)
        if truths:result.append(Flow(store.copy(),value=Value(bools=truths)))
        if value.top or value.top_sorts-known_sorts:
            for flow in self.unknown(store,node,"unknown_truth_protocol"):
                result.append(Flow(flow.store,value=Value(bools=frozenset((False,True)))) if flow.kind=="normal" else flow)
        for ref in value.refs:
            obj=store.heap.get(ref)
            if obj and obj.kind=="instance":
                hook=self.ops.join_all(self.lookup_class(cls,"__bool__",store) for cls in obj.class_ids);length=False
                if hook.is_bottom():hook=self.ops.join_all(self.lookup_class(cls,"__len__",store) for cls in obj.class_ids);length=True
                if not hook.is_bottom():
                    current=store.copy();bound=self.bound(hook,Value.ref(ref),current,frame,node)
                    for returned in self.invoke(bound,[],(),current,frame,node):
                        if returned.kind!="normal":result.append(returned);continue
                        answer=returned.value
                        if not length:
                            boolean=answer.bools | (frozenset((False,True)) if answer.top or "bool" in answer.top_sorts else frozenset())
                            if boolean:result.append(Flow(returned.store.copy(),value=Value(bools=boolean)))
                            remainder=replace(answer,bools=frozenset(),top=False,top_sorts=answer.top_sorts-{"bool"})
                            if answer.top or not remainder.is_bottom():result.append(self.error(returned.store.copy(),"TypeError"))
                        else:
                            integers=answer.ints
                            if answer.bools:integers=self.ops.integer.join(integers,self.ops.join_all(Value.constant(int(x)) for x in answer.bools).ints)
                            if answer.top or "int" in answer.top_sorts:integers=self.ops.integer.top()
                            if not integers.is_bottom:
                                if integers.lo is None or integers.lo<0:result.append(self.error(returned.store.copy(),"ValueError"))
                                allowed=integers.refine_ge(0)
                                if not allowed.is_bottom:
                                    boolean=self.ops.truth(Value(ints=allowed))
                                    if boolean:result.append(Flow(returned.store.copy(),value=Value(bools=boolean)))
                            remainder=replace(answer,ints=self.ops.integer.bottom(),bools=frozenset(),top=False,top_sorts=answer.top_sorts-{"int","bool"})
                            if answer.top or not remainder.is_bottom():result.append(self.error(returned.store.copy(),"TypeError"))
                    continue
            if obj and obj.kind in {"list","tuple","dict","set","range"}:
                length=obj.metadata.get("length")
                booleans=frozenset((bool(length),)) if type(length) is int and not obj.havoc and obj.default.is_bottom() else frozenset((False,True))
                result.append(Flow(store.copy(),value=Value(bools=booleans)))
            elif obj is None and not ref.startswith("builtin:"):
                for flow in self.unknown(store,node,"unknown_truth_receiver"):
                    result.append(Flow(flow.store,value=Value(bools=frozenset((False,True)))) if flow.kind=="normal" else flow)
            else:result.append(Flow(store.copy(),value=Value.constant(True)))
        return self.d.compact(result)

    def refine_test(self,node,store,frame,truth):
        if isinstance(node,ast.Compare) and len(node.ops)==1 and isinstance(node.left,ast.Name) and len(node.comparators)==1 and isinstance(node.comparators[0],ast.Constant):
            cell=frame.cell(node.left.id);old=store.cells.get(cell,Value(unbound=True));new=self.ops.refine(old,node.ops[0],node.comparators[0].value,truth)
            if new.is_bottom():return None
            store.cells[cell]=new
        elif isinstance(node,ast.Name):
            cell=frame.cell(node.id);old=store.cells.get(cell,Value(unbound=True));constants=old.exact_constants()
            if constants is not None:
                new=self.ops.join_all(Value.constant(x) for x in constants if bool(x)==truth)
                if new.is_bottom():return None
                store.cells[cell]=new
        elif isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.Not):return self.refine_test(node.operand,store,frame,not truth)
        return store

    def assign(self,target,value,store,frame,node):
        if isinstance(target,ast.Name):
            self.write_name(target.id,value,store,frame,node);return [self.normal(store)]
        if isinstance(target,(ast.Tuple,ast.List)):
            sequences=[]
            for ref in value.refs:
                obj=store.heap.get(ref)
                if obj and obj.kind in ("list","tuple","range") and "length" in obj.metadata:sequences.append([obj.fields.get(i,obj.default) for i in range(obj.metadata["length"])])
            if not sequences:
                self.d.open("unknown_unpack",store,node)
                sequence=[Value.unknown() for _ in target.elts]
                sequences=[sequence]
            result=[]
            for sequence in sequences:
                star=next((i for i,x in enumerate(target.elts) if isinstance(x,ast.Starred)),None)
                if star is None and len(sequence)!=len(target.elts):result.append(self.error(store.copy(),"ValueError"));continue
                if star is not None and len(sequence)<len(target.elts)-1:result.append(self.error(store.copy(),"ValueError"));continue
                s=store.copy();pairs=[]
                if star is None:pairs=list(zip(target.elts,sequence))
                else:
                    tail=len(target.elts)-star-1;middle=sequence[star:len(sequence)-tail if tail else None]
                    ref=self.alloc(target,s,frame,"list",{i:v for i,v in enumerate(middle)},{"length":len(middle)})
                    pairs=list(zip(target.elts[:star],sequence[:star]))+[(target.elts[star].value,ref)]+list(zip(target.elts[star+1:],sequence[len(sequence)-tail:] if tail else []))
                flows=[self.normal(s)]
                for item,v in pairs:
                    flows=[out for flow in flows for out in (self.assign(item,v,flow.store,frame,node) if flow.kind=="normal" else [flow])]
                result.extend(flows)
            return result
        if isinstance(target,ast.Attribute):
            result=[]
            for flow in self.expr(target.value,store,frame):
                if flow.kind!="normal":result.append(flow)
                else:result.extend(self.setattr(flow.value,target.attr,value,flow.store,frame,node))
            return result
        if isinstance(target,ast.Subscript):
            states,errors=self.eval_nodes((target.value,target.slice),store,frame);result=list(errors)
            for s,(base,key) in states:result.extend(self.setitem(base,key,value,s,frame,node))
            return result
        return self.unknown(store,node,"unsupported_assignment:"+type(target).__name__)

    def statement(self,node,store,frame):
        self.d.tick(node)
        if isinstance(node,ast.Expr):
            return [Flow(f.store,f.kind,Value.constant(None) if f.kind=="normal" else f.value,f.exception) for f in self.expr(node.value,store,frame)]
        if isinstance(node,(ast.Assign,ast.AnnAssign)):
            if isinstance(node,ast.AnnAssign) and node.value is None:return [self.normal(store.copy())]
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            result=[]
            for flow in self.expr(node.value,store,frame):
                if flow.kind!="normal":
                    if flow.kind=="suspend" and len(targets)==1:flow=replace(flow,resume_target=targets[0])
                    result.append(flow);continue
                pending=[flow]
                for target in targets:
                    pending=[out for f in pending for out in (self.assign(target,flow.value,f.store,frame,node) if f.kind=="normal" else [f])]
                    if self.d.oracle is not None:
                        if isinstance(target,ast.Name) and isinstance(node.value,ast.Name) and hasattr(self.d.oracle,"register_copy"):
                            self.d.oracle.register_copy(frame.cell(node.value.id),frame.cell(target.id))
                        elif isinstance(target,ast.Name) and isinstance(node.value,ast.Subscript) and isinstance(node.value.value,ast.Name) and isinstance(node.value.slice,ast.Constant) and hasattr(self.d.oracle,"register_load"):
                            self.d.oracle.register_load(frame.cell(node.value.value.id),node.value.slice.value,frame.cell(target.id))
                result.extend(pending)
            return result
        if isinstance(node,ast.AugAssign):
            if not isinstance(node.target,ast.Name):
                return self.unknown(store,node,"complex_augmented_assignment")
            result=[]
            for old in self.expr(node.target,store,frame):
                if old.kind!="normal":result.append(old);continue
                for new in self.expr(node.value,old.store,frame):
                    if new.kind!="normal":result.append(new);continue
                    value,errors=self.ops.scalar_binary(node.op,old.value,new.value)
                    if not value.is_bottom():result.extend(self.assign(node.target,value,new.store,frame,node))
                    for error in errors:
                        if error.startswith(("unknown_","resource_")):
                            for opaque in self.unknown(new.store,node,error):
                                if opaque.kind=="normal":result.extend(self.assign(node.target,Value.unknown(),opaque.store,frame,node))
                                else:result.append(opaque)
                        else:result.append(self.error(new.store.copy(),error))
            return result
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            result=[]
            for flow in self.define_function(node,store,frame,node.name):
                if flow.kind=="normal":self.write_name(node.name,flow.value,flow.store,frame,node)
                result.append(self.normal(flow.store) if flow.kind=="normal" else flow)
            return result
        if isinstance(node,ast.ClassDef):return self.define_class(node,store,frame)
        if isinstance(node,ast.Return):
            return [Flow(f.store,"return",f.value) if f.kind=="normal" else f for f in self.expr(node.value,store,frame)]
        if isinstance(node,ast.Raise):
            if node.cause is not None:
                return [Flow(flow.store,"raise",Value.unknown(),"AnyException") if flow.kind=="normal" else flow for flow in self.unknown(store,node,"exception_cause_protocol")]
            if node.exc is None:
                value=store.cells.get("$active_exception",Value.bottom());kinds=store.cells.get("$active_exception_type",Value.bottom()).exact_constants()
                if value.is_bottom() or not kinds:return [self.error(store.copy(),"RuntimeError")]
                return [Flow(store.copy(),"raise",value,kind) for kind in kinds]
            result=[]
            for flow in self.expr(node.exc,store,frame):
                if flow.kind!="normal":result.append(flow)
                else:result.extend(self.raise_value(flow.value,flow.store,frame,node))
            return result
        if isinstance(node,ast.If):
            result=[]
            for flow in self.expr(node.test,store,frame):
                if flow.kind!="normal":result.append(flow);continue
                for tested in self.truth_flows(flow.value,flow.store,frame,node):
                    if tested.kind!="normal":result.append(tested);continue
                    for truth,body in ((True,node.body),(False,node.orelse)):
                        if truth in tested.value.bools:
                            current=self.refine_test(node.test,tested.store.copy(),frame,truth)
                            if current is not None:result.extend(self.block(body,[self.normal(current)],frame))
            return result
        if isinstance(node,ast.While):return self.while_loop(node,store,frame)
        if isinstance(node,(ast.For,ast.AsyncFor)):return self.for_loop(node,store,frame)
        if isinstance(node,ast.Break):return [Flow(store.copy(),"break")]
        if isinstance(node,ast.Continue):return [Flow(store.copy(),"continue")]
        if isinstance(node,ast.Try):return self.try_statement(node,store,frame)
        if isinstance(node,(ast.With,ast.AsyncWith)):return self.with_statement(node,store,frame)
        if isinstance(node,ast.Assert):
            result=[]
            for flow in self.expr(node.test,store,frame):
                if flow.kind!="normal":result.append(flow);continue
                for tested in self.truth_flows(flow.value,flow.store,frame,node):
                    if tested.kind!="normal":result.append(tested);continue
                    if True in tested.value.bools:
                        current=self.refine_test(node.test,tested.store.copy(),frame,True)
                        if current is not None:result.append(self.normal(current))
                    if False in tested.value.bools:
                        messages=self.expr(node.msg,tested.store.copy(),frame) if node.msg is not None else [self.normal(tested.store.copy())]
                        for message in messages:result.append(self.error(message.store,"AssertionError") if message.kind=="normal" else message)
            return result
        if isinstance(node,(ast.Import,ast.ImportFrom)):return self.import_statement(node,store,frame)
        if isinstance(node,ast.Delete):
            result=[self.normal(store.copy())]
            for target in node.targets:
                next_result=[]
                for flow in result:
                    if flow.kind!="normal":next_result.append(flow);continue
                    if isinstance(target,ast.Name):self.write_name(target.id,Value(unbound=True),flow.store,frame,node);next_result.append(flow)
                    elif isinstance(target,ast.Attribute):
                        for base in self.expr(target.value,flow.store,frame):
                            if base.kind!="normal":next_result.append(base);continue
                            for ref in base.value.refs:
                                if ref in base.store.heap:
                                    base.store.heap[ref].fields.pop(target.attr,None);base.store.heap[ref].maybe_missing.add(target.attr);base.store.writes.add(("heap",ref,target.attr))
                            next_result.append(self.normal(base.store))
                    else:next_result.extend(self.unknown(flow.store,node,"unsupported_delete"))
                result=next_result
            return result
        if isinstance(node,(ast.Pass,ast.Global,ast.Nonlocal)):return [self.normal(store.copy())]
        return self.unknown(store,node,"unsupported_statement:"+type(node).__name__)

    def define_function(self,node,store,frame,name):
        decorators=getattr(node,"decorator_list",())
        decstates,errors=self.eval_nodes(decorators,store,frame)
        result=list(errors)
        for ds,decvalues in decstates:
            annotations=[(arg.arg,arg.annotation) for arg in (*node.args.posonlyargs,*node.args.args,*node.args.kwonlyargs) if arg.annotation is not None]
            if node.args.vararg and node.args.vararg.annotation is not None:annotations.append((node.args.vararg.arg,node.args.vararg.annotation))
            if node.args.kwarg and node.args.kwarg.annotation is not None:annotations.append((node.args.kwarg.arg,node.args.kwarg.annotation))
            if getattr(node,"returns",None) is not None:annotations.append(("return",node.returns))
            defaults_count=len(node.args.defaults)+sum(x is not None for x in node.args.kw_defaults)
            values=[*node.args.defaults,*[x for x in node.args.kw_defaults if x is not None]]
            if frame.module not in self.d.future_annotations:values.extend(annotation for _,annotation in annotations)
            states,ex=self.eval_nodes(values,ds,frame);result.extend(ex)
            for s,defaults in states:
                count=len(node.args.defaults);kw_defaults=[];index=count
                for arg,default in zip(node.args.kwonlyargs,node.args.kw_defaults):
                    if default is not None:kw_defaults.append((arg.arg,defaults[index]));index+=1
                locals_,globals_,nonlocals_,has_yield=classify_bindings(node)
                identifier=f"function:{self.d.point(node)}@{'|'.join(frame.context)}"
                closure=dict(frame.closure)
                if not frame.is_module and not frame.is_class:
                    closure.update((n,frame.cell(n)) for n in frame.locals)
                if frame.is_class:
                    classcell=f"classcell:{frame.identity}";s.cells[classcell]=Value.ref(frame.identity);closure["__class__"]=classcell
                qualname=(frame.module+"."+name) if frame.is_module else frame.qualname+"."+name
                info=FunctionInfo(identifier,node,frame.module,qualname,tuple(sorted(closure.items())),tuple(defaults[:count]),tuple(kw_defaults),locals_,globals_,nonlocals_,has_yield,isinstance(node,ast.AsyncFunctionDef))
                self.d.functions[identifier]=info
                function_fields={f"$default:{i}":value for i,value in enumerate(info.defaults)}
                function_fields.update(("$kwdefault:"+key,value) for key,value in info.kw_defaults)
                if frame.module in self.d.future_annotations:function_fields.update(("$annotation:"+name,Value.constant(ast.unparse(annotation))) for name,annotation in annotations)
                else:function_fields.update(("$annotation:"+name,value) for (name,_),value in zip(annotations,defaults[defaults_count:]))
                created=HeapObject("function",function_fields,metadata={"qualname":qualname,"default_count":len(info.defaults),"closure":info.closure})
                if identifier in s.heap:
                    created=s.heap[identifier].join(created,self.ops);created.singleton=False
                s.heap[identifier]=created
                flows=[Flow(s,value=Value.ref(identifier))]
                for decorator in reversed(decvalues):
                    flows=[out for flow in flows for out in (self.invoke(decorator,[flow.value],(),flow.store,frame,node) if flow.kind=="normal" else [flow])]
                result.extend(flows)
        return result

    def exception_class_tags(self,identifier,store,seen=None):
        seen=set() if seen is None else seen
        if identifier in seen:return set()
        seen.add(identifier)
        if identifier.startswith("builtin:"):
            return {"builtin:"+canonical_builtin_exception_name(name) for name in builtin_exception_ancestors(identifier.split(":",1)[1])}
        obj=store.heap.get(identifier)
        if obj is None or obj.kind!="class":return set()
        tags={identifier}
        for base in obj.metadata.get("bases",()):tags|=self.exception_class_tags(base,store,seen)
        return tags

    def raised_tags(self,flow):
        if flow.exception=="AnyException":return None
        result=set()
        for ref in flow.value.refs:
            obj=flow.store.heap.get(ref)
            if obj:
                for cls in obj.class_ids:result|=self.exception_class_tags(cls,flow.store)
                for name in obj.metadata.get("exception_types",()):
                    if name.startswith(("builtin:","class:")):result.add(name)
                    else:result.update("builtin:"+canonical_builtin_exception_name(x) for x in builtin_exception_ancestors(name))
        if not result:result.update("builtin:"+canonical_builtin_exception_name(x) for x in builtin_exception_ancestors(flow.exception))
        return result

    def handler_tags(self,value,store):
        tags=set();uncertain=value.top or bool(value.top_sorts);invalid=bool(value.bools or value.none or value.floats or not value.ints.is_bottom or not value.strings.is_bottom())
        for ref in value.refs:
            if ref.startswith("builtin:") and ref.split(":",1)[1] in EXCEPTION_NAMES:
                tags.add("builtin:"+canonical_builtin_exception_name(ref.split(":",1)[1]));continue
            obj=store.heap.get(ref)
            if obj and obj.kind=="class" and "builtin:BaseException" in self.exception_class_tags(ref,store):tags.add(ref)
            elif obj and obj.kind=="tuple":
                for item in obj.fields.values():
                    nested,unknown,bad=self.handler_tags(item,store);tags|=nested;uncertain|=unknown;invalid|=bad
                if not obj.default.is_bottom():uncertain=True
            else:invalid=True
        return tags,uncertain,invalid

    def raise_value(self,value,store,frame,node,constructed=False):
        result=[]
        if value.top or value.top_sorts:
            for flow in self.unknown(store,node,"unknown_raised_object"):
                if flow.kind=="normal":result.append(Flow(flow.store,"raise",Value.unknown(),"AnyException"))
                else:result.append(flow)
        for ref in value.refs:
            obj=store.heap.get(ref)
            is_class=(ref.startswith("builtin:") and ref.split(":",1)[1] in EXCEPTION_NAMES) or obj and obj.kind=="class"
            if is_class:
                if constructed or "builtin:BaseException" not in self.exception_class_tags(ref,store):result.append(self.error(store.copy(),"TypeError"));continue
                for made in self.invoke(Value.ref(ref),[],(),store.copy(),frame,node):
                    if made.kind=="normal":result.extend(self.raise_value(made.value,made.store,frame,node,True))
                    else:result.append(made)
                continue
            if obj:
                tags=set().union(*(self.exception_class_tags(cls,store) for cls in obj.class_ids)) if obj.class_ids else set()
                names=obj.metadata.get("exception_types",())
                if obj.kind=="exception" or "builtin:BaseException" in tags:
                    name=obj.metadata.get("exception_name")
                    if name is None:name=next((store.heap[cls].metadata.get("name","BaseException") for cls in obj.class_ids if cls in store.heap),"BaseException")
                    self.d.exceptions.add(name);result.append(Flow(store.copy(),"raise",Value.ref(ref),name));continue
            result.append(self.error(store.copy(),"TypeError"))
        scalars=replace(value,refs=frozenset(),top=False,top_sorts=frozenset())
        if not scalars.is_bottom():result.append(self.error(store.copy(),"TypeError"))
        return result

    def _active_exception(self,store,raised):
        previous=(store.cells.get("$active_exception"),store.cells.get("$active_exception_type"))
        store.cells["$active_exception"]=raised.value;store.cells["$active_exception_type"]=Value.constant(raised.exception)
        return previous
    def _restore_exception(self,store,previous):
        for name,value in zip(("$active_exception","$active_exception_type"),previous):
            if value is None:store.cells.pop(name,None)
            else:store.cells[name]=value

    def try_statement(self,node,store,frame):
        protected=self.block(node.body,[self.normal(store.copy())],frame);result=[]
        for flow in protected:
            if flow.kind=="normal":result.extend(self.block(node.orelse,[flow],frame));continue
            if flow.kind!="raise":result.append(flow);continue
            remaining=[flow]
            for handler in node.handlers:
                next_remaining=[]
                for raised in remaining:
                    evaluations=[Flow(raised.store.copy(),value=Value.bottom())] if handler.type is None else self.expr(handler.type,raised.store.copy(),frame)
                    for evaluated in evaluations:
                        if evaluated.kind!="normal":result.append(evaluated);continue
                        tags,uncertain,invalid=({"builtin:BaseException"},False,False) if handler.type is None else self.handler_tags(evaluated.value,evaluated.store)
                        raised_types=self.raised_tags(raised)
                        matches=bool(tags & raised_types) if raised_types is not None else bool(tags)
                        may_escape=not matches or uncertain or raised_types is None and "builtin:BaseException" not in tags or raised_types is not None and any(tag not in raised_types for tag in tags)
                        if uncertain:self.d.open("unknown_exception_handler",evaluated.store,handler)
                        if invalid:result.append(self.error(evaluated.store.copy(),"TypeError"))
                        if matches or uncertain:
                            current=evaluated.store.copy();previous=self._active_exception(current,raised)
                            if handler.name:self.write_name(handler.name,raised.value,current,frame,handler)
                            for handled in self.block(handler.body,[self.normal(current)],frame):
                                if handler.name:self.write_name(handler.name,Value(unbound=True),handled.store,frame,handler)
                                self._restore_exception(handled.store,previous);result.append(handled)
                        if may_escape and (tags or uncertain):next_remaining.append(replace(raised,store=evaluated.store))
                remaining=next_remaining
            result.extend(remaining)
        if node.finalbody:
            cleaned=[]
            for pending in result:
                if pending.kind=="suspend":cleaned.append(replace(pending,cleanups=(*pending.cleanups,tuple(node.finalbody))));continue
                current=pending.store.copy();previous=self._active_exception(current,pending) if pending.kind=="raise" else None
                for cleanup in self.block(node.finalbody,[self.normal(current)],frame):
                    if previous is not None:self._restore_exception(cleanup.store,previous)
                    cleaned.append(resume_pending(pending,cleanup))
            result=cleaned
        return result

    def while_loop(self,node,store,frame):
        initial=store.copy();invariant=initial.copy();delay=int(self.d.abstract.get("widen_delay",2));limit=int(self.d.abstract.get("max_stmt_visits",100))
        def step(head):
            back=[];exits=[];escapes=[]
            for tested in self.expr(node.test,head.copy(),frame):
                if tested.kind!="normal":escapes.append(tested);continue
                for truth_flow in self.truth_flows(tested.value,tested.store,frame,node):
                    if truth_flow.kind!="normal":escapes.append(truth_flow);continue
                    if False in truth_flow.value.bools:
                        s=self.refine_test(node.test,truth_flow.store.copy(),frame,False)
                        if s is not None:exits.extend(self.block(node.orelse,[self.normal(s)],frame))
                    if True in truth_flow.value.bools:
                        s=self.refine_test(node.test,truth_flow.store.copy(),frame,True)
                        if s is not None:
                            for out in self.block(node.body,[self.normal(s)],frame):
                                if out.kind in ("normal","continue"):back.append(out.store)
                                elif out.kind=="break":exits.append(self.normal(out.store))
                                else:escapes.append(out)
            return back,exits,escapes
        stable=False
        for iteration in range(limit):
            self.d.stats["loop_iterations"]+=1
            back,exits,escapes=step(invariant)
            candidate=initial.copy()
            for s in back:candidate=candidate.join(s,self.ops)
            if candidate.leq(invariant,self.ops):stable=True;break
            invariant=invariant.join(candidate,self.ops,widen=iteration>=delay)
        if not stable:
            self.d.open("loop_budget",invariant,node)
            return self.d.compact(exits+escapes+[self.normal(self.havoc_loop_names(node,invariant,frame))])
        if self.d.abstract.get("enable_narrowing",True):
            for _ in range(int(self.d.abstract.get("narrowing_iterations",2))):
                back,_,_=step(invariant);candidate=initial.copy()
                for s in back:candidate=candidate.join(s,self.ops)
                if not candidate.leq(invariant,self.ops):break
                checkback,_,_=step(candidate);check=initial.copy()
                for s in checkback:check=check.join(s,self.ops)
                if check.leq(candidate,self.ops):
                    invariant=candidate;self.d.stats["narrowing_checks_passed"]+=1
                else:self.d.stats["narrowing_checks_failed"]+=1;break
        _,exits,escapes=step(invariant)
        return self.d.compact(exits+escapes)

    def havoc_loop_names(self,node,store,frame):
        result=store.copy()
        for item in ast.walk(node):
            if isinstance(item,ast.Name) and isinstance(item.ctx,ast.Store):result.write_cell(frame.cell(item.id),Value.unknown())
        return result

    def iterable_values(self,value,store):
        sequences=[];unknown=value.top or bool(value.top_sorts)
        for ref in value.refs:
            obj=store.heap.get(ref)
            if obj and obj.havoc:unknown=True;continue
            if obj and obj.kind in ("list","tuple","set","range") and "length" in obj.metadata:
                sequences.append([obj.fields.get(i,obj.default) for i in range(obj.metadata["length"])])
            elif obj and obj.kind=="dict" and obj.default.is_bottom() and not obj.havoc:sequences.append([Value.constant(k) for k in obj.fields])
            else:unknown=True
        strs=value.strings.get_consts()
        if strs is not None:
            for s in strs:sequences.append([Value.constant(c) for c in s])
        elif not value.strings.is_bottom():unknown=True
        return sequences,unknown

    def for_loop(self,node,store,frame):
        result=[]
        for iterable in self.expr(node.iter,store,frame):
            if iterable.kind!="normal":result.append(iterable);continue
            sequences,unknown=self.iterable_values(iterable.value,iterable.store)
            for sequence in sequences:
                flows=[self.normal(iterable.store.copy())];breaks=[];escapes=[]
                for value in sequence:
                    iteration=[]
                    for flow in flows:
                        for assigned in self.assign(node.target,value,flow.store,frame,node):
                            if assigned.kind!="normal":escapes.append(assigned);continue
                            for out in self.block(node.body,[assigned],frame):
                                if out.kind in ("normal","continue"):iteration.append(self.normal(out.store))
                                elif out.kind=="break":breaks.append(self.normal(out.store))
                                else:escapes.append(out)
                    flows=self.d.compact(iteration)
                    if not flows:break
                result.extend(self.block(node.orelse,flows,frame)+breaks+escapes)
            if unknown:
                self.d.open("unknown_iteration",iterable.store,node)
                # Zero iterations and arbitrary many iterations both remain possible.
                result.extend(self.block(node.orelse,[self.normal(iterable.store.copy())],frame))
                s=iterable.store.copy()
                for assigned in self.assign(node.target,Value.unknown(),s,frame,node):
                    if assigned.kind!="normal":result.append(assigned);continue
                    body=self.block(node.body,[assigned],frame)
                    for out in body:
                        if out.kind in ("normal","continue","break"):result.append(self.normal(self.havoc_loop_names(node,out.store,frame)))
                        else:result.append(out)
        return self.d.compact(result)

    def call(self,node,store,frame):
        result=[]
        for called in self.expr(node.func,store,frame):
            if called.kind!="normal":result.append(called);continue
            items=[("arg",arg.value if isinstance(arg,ast.Starred) else arg,isinstance(arg,ast.Starred)) for arg in node.args]
            items += [("keyword",kw.value,kw.arg) for kw in node.keywords]
            items.sort(key=lambda item:(getattr(item[1],"lineno",0),getattr(item[1],"col_offset",0)))
            paths=[(called.store,[],[],False)]
            for kind,expr,flag in items:
                next_paths=[]
                for s,args,keywords,uncertain in paths:
                    for flow in self.expr(expr,s,frame):
                        if flow.kind!="normal":result.append(flow);continue
                        a=list(args);k=list(keywords);unknown=uncertain
                        if kind=="arg":
                            if flag:
                                sequences,broad=self.iterable_values(flow.value,flow.store)
                                for sequence in sequences:next_paths.append((flow.store.copy(),a+sequence,list(k),unknown))
                                if broad:self.d.open("unknown_star_arguments",flow.store,node);next_paths.append((flow.store,a,k,True))
                                continue
                            a.append(flow.value)
                        elif flag is None:
                            expanded=False
                            for ref in flow.value.refs:
                                obj=flow.store.heap.get(ref)
                                if obj and obj.kind=="dict":
                                    if any(type(key) is not str for key in obj.fields):result.append(self.error(flow.store.copy(),"TypeError"))
                                    else:k.extend(obj.fields.items());expanded=True
                                    if not obj.default.is_bottom() or obj.havoc:unknown=True
                            if not expanded:unknown=True
                            if unknown:self.d.open("unknown_keyword_arguments",flow.store,node)
                        else:k.append((flag,flow.value))
                        next_paths.append((flow.store,a,k,unknown))
                paths=next_paths
            for s,args,keywords,uncertain in paths:
                if uncertain:
                    result.append(self.error(s.copy(),"TypeError"))
                    for identifier in called.value.refs:
                        info=self.d.functions.get(identifier)
                        if info:
                            expected=[x.arg for x in (*info.node.args.posonlyargs,*info.node.args.args)]
                            filled=args+[Value.unknown() for _ in expected[len(args):]]
                            kw=list(keywords)+[(x.arg,Value.unknown()) for x in info.node.args.kwonlyargs if x.arg not in dict(keywords)]
                            result.extend(self.d.request_function(identifier,filled,kw,s.copy(),frame,node))
                        else:result.extend(self.unknown(s,node,"unknown_argument_shape"))
                else:result.extend(self.invoke(called.value,args,keywords,s,frame,node))
        return self.d.compact(result)

    def invoke(self,callee,args,keywords,store,frame,node):
        result=[]
        if callee.top or callee.top_sorts:result.extend(self.unknown(store,node,"unknown_call"))
        for identifier in sorted(callee.refs):
            if identifier in self.d.functions:
                returned=self.d.request_function(identifier,args,keywords,store.copy(),frame,node)
                tokens=frozenset(token for token in callee.relations if token.handler in {identifier,self.d.functions[identifier].qualname})
                result.extend(replace(flow,value=replace(flow.value,relations=flow.value.relations|tokens)) for flow in returned)
                continue
            if identifier.startswith("builtin:"):
                name=identifier.split(':',1)[1];self.d.calls.add((frame.qualname,"builtins."+name,self.d.point(node)))
                result.extend(self.builtin(name,args,keywords,store.copy(),frame,node));continue
            obj=store.heap.get(identifier)
            if obj is None:result.extend(self.unknown(store,node,"unresolved_callable:"+identifier));continue
            if obj.kind=="bound_method":
                target=obj.metadata["callable"];receiver=obj.metadata["receiver"]
                result.extend(self.invoke(target,[receiver,*args],keywords,store.copy(),frame,node))
            elif obj.kind=="builtin_method":result.extend(self.builtin_method(obj.metadata["name"],obj.metadata["receiver"],args,keywords,store.copy(),frame,node))
            elif obj.kind=="class":result.extend(self.construct(identifier,args,keywords,store.copy(),frame,node))
            elif obj.kind=="staticmethod":result.extend(self.invoke(obj.fields["callable"],args,keywords,store.copy(),frame,node))
            elif obj.kind=="instance":
                for call in self.getattr(Value.ref(identifier),"__call__",store.copy(),frame,node):
                    if call.kind=="normal":result.extend(self.invoke(call.value,args,keywords,call.store,frame,node))
                    else:result.append(call)
            else:result.append(self.error(store.copy(),"TypeError"))
        if not callee.top and not callee.top_sorts and not callee.refs and not callee.is_bottom():result.append(self.error(store.copy(),"TypeError"))
        return result

    def lookup_class(self,clsid,name,store,seen=None):
        seen=set() if seen is None else seen
        if clsid in seen:return Value.bottom()
        seen.add(clsid);cls=store.heap.get(clsid)
        if cls is None:
            if clsid=="builtin:object" or clsid.startswith("builtin:") and clsid.split(":",1)[1] in EXCEPTION_NAMES:return Value.bottom()
            self.d.open("unmodeled_builtin_class_lookup:"+clsid);return Value.unknown()
        if cls.havoc:
            self.d.open("havoc_class_lookup");return Value.unknown()
        if name in cls.fields:return cls.fields[name]
        for base in cls.metadata.get("mro",cls.metadata.get("bases",())):
            if base==clsid:continue
            value=self.lookup_class(base,name,store,seen)
            if not value.is_bottom():return value
        return cls.default if not cls.havoc else Value.unknown()

    def bound(self,value,receiver,store,frame,node):
        scalar=replace(value,refs=frozenset())
        outputs=[] if scalar.is_bottom() else [scalar]
        for ref in value.refs:
            obj=store.heap.get(ref)
            if ref in self.d.functions:
                identity=f"bound:{ref}:{','.join(sorted(receiver.refs))}:{self.d.point(node)}"
                store.heap[identity]=HeapObject("bound_method",metadata={"callable":Value.ref(ref),"receiver":receiver})
                outputs.append(Value.ref(identity))
            elif obj and obj.kind=="staticmethod":outputs.append(obj.fields["callable"])
            elif obj and obj.kind=="classmethod":
                classes=set()
                for r in receiver.refs:
                    target=store.heap.get(r)
                    if target:classes|=set(target.class_ids) if target.kind!="class" else {r}
                identity=f"bound:{ref}:{','.join(sorted(classes))}:{self.d.point(node)}"
                store.heap[identity]=HeapObject("bound_method",metadata={"callable":obj.fields["callable"],"receiver":Value(refs=frozenset(classes))})
                outputs.append(Value.ref(identity))
            else:outputs.append(Value.ref(ref))
        if value.top:outputs.append(Value.unknown())
        return self.ops.join_all(outputs)

    def getattr(self,base,name,store,frame,node):
        result=[]
        if base.top or base.top_sorts:result.extend(self.unknown(store,node,"unknown_attribute:"+name,havoc=False))
        for ref in base.refs:
            obj=store.heap.get(ref)
            if ref.startswith("builtin:"):
                identity=f"builtin-method:{ref}:{name}";s=store.copy();s.heap[identity]=HeapObject("builtin_method",metadata={"name":name,"receiver":Value.ref(ref)});result.append(Flow(s,value=Value.ref(identity)));continue
            if obj is None:result.extend(self.unknown(store,node,"unknown_object_attribute:"+name,havoc=False));continue
            s=store.copy()
            if obj.kind=="module":
                value=s.effective_cell(f"global:{obj.metadata['module']}:{name}")
                if not value.without_unbound().is_bottom():result.append(Flow(s.copy(),value=value.without_unbound()))
                if value.unbound:result.append(self.error(s,"AttributeError"))
                continue
            if obj.kind in ("list","dict","tuple","set","str","generator","coroutine","iterator","property"):
                allowed={"append","extend","insert","pop","clear","get","setdefault","update","keys","values","items","copy","__iter__","__next__","send","throw","close","__await__","setter","deleter","getter"}
                if name in allowed:
                    identity=f"builtin-method:{ref}:{name}:{self.d.point(node)}";s.heap[identity]=HeapObject("builtin_method",metadata={"name":name,"receiver":Value.ref(ref)})
                    result.append(Flow(s,value=Value.ref(identity)));continue
            if obj.kind in {"list","dict","tuple","set","range","iterator","generator","coroutine"}:
                if known_builtin_attribute(obj.kind,name):result.extend(self.unknown(s,node,"unmodeled_builtin_attribute:"+obj.kind+"."+name,havoc=False))
                else:result.append(self.error(s,"AttributeError"))
                continue
            if obj.kind=="super":
                owners=obj.metadata["class"].refs;receiver=obj.metadata["receiver"];value=Value.bottom()
                for instance in receiver.refs:
                    target=s.heap.get(instance)
                    for clsid in target.class_ids if target else ():
                        cls=s.heap.get(clsid);mro=(clsid,*cls.metadata.get("mro",())) if cls else ()
                        for owner in owners:
                            if owner in mro:
                                for basecls in mro[mro.index(owner)+1:]:
                                    candidate=s.heap.get(basecls)
                                    if candidate and name in candidate.fields:value=self.ops.join(value,candidate.fields[name]);break
                if value.is_bottom():result.append(self.error(s,"AttributeError"))
                else:result.append(Flow(s,value=self.bound(value,receiver,s,frame,node)))
                continue
            if obj.kind=="class":
                value=self.lookup_class(ref,name,s)
                if not value.is_bottom():
                    scalar=replace(value,refs=frozenset())
                    resolved=[] if scalar.is_bottom() else [scalar]
                    for attrref in value.refs:
                        descriptor=s.heap.get(attrref)
                        if descriptor and descriptor.kind=="classmethod":resolved.append(self.bound(Value.ref(attrref),Value.ref(ref),s,frame,node))
                        elif descriptor and descriptor.kind=="staticmethod":resolved.append(descriptor.fields["callable"])
                        elif descriptor and descriptor.kind=="instance" and any(not self.lookup_class(cls,"__get__",s).is_bottom() for cls in descriptor.class_ids):
                            for method in self.getattr(Value.ref(attrref),"__get__",s.copy(),frame,node):
                                if method.kind=="normal":result.extend(self.invoke(method.value,[Value.constant(None),Value.ref(ref)],(),method.store,frame,node))
                                else:result.append(method)
                        else:resolved.append(Value.ref(attrref))
                    if resolved:result.append(Flow(s,value=self.ops.join_all(resolved)))
                else:result.append(self.error(s,"AttributeError"))
                continue
            if obj.kind=="instance":
                custom=self.ops.join_all(self.lookup_class(cls,"__getattribute__",s) for cls in obj.class_ids)
                if not custom.is_bottom():
                    result.extend(self.invoke(self.bound(custom,Value.ref(ref),s,frame,node),[Value.constant(name)],(),s,frame,node));continue
            descriptor=Value.bottom()
            for cls in obj.class_ids:descriptor=self.ops.join(descriptor,self.lookup_class(cls,name,s))
            if descriptor.top:
                result.extend(self.unknown(s,node,"unknown_class_descriptor"));continue
            data=[]
            for descid in descriptor.refs:
                desc=s.heap.get(descid)
                if desc and desc.kind=="property":data.append(descid)
                elif desc and any(not self.lookup_class(cls,"__set__",s).is_bottom() or not self.lookup_class(cls,"__delete__",s).is_bottom() for cls in desc.class_ids):data.append(descid)
            if data:
                for descid in data:
                    desc=s.heap[descid]
                    if desc.kind=="property":
                        getter=desc.fields.get("fget",Value.bottom())
                        if getter.is_bottom():result.append(self.error(s.copy(),"AttributeError"))
                        else:result.extend(self.invoke(getter,[Value.ref(ref)],(),s.copy(),frame,node))
                    else:
                        for method in self.getattr(Value.ref(descid),"__get__",s.copy(),frame,node):
                            if method.kind=="normal":result.extend(self.invoke(method.value,[Value.ref(ref),Value(refs=obj.class_ids)],(),method.store,frame,node))
                            else:result.append(method)
                continue
            value,missing=s.read_slot(ref,name,self.ops)
            self.d.reads.add((ref,name,self.d.point(node)))
            if not value.is_bottom():result.append(Flow(s.copy(),value=value))
            if missing or value.is_bottom():
                if not descriptor.is_bottom():
                    scalar_descriptor=replace(descriptor,refs=frozenset())
                    if not scalar_descriptor.is_bottom():result.append(Flow(s.copy(),value=scalar_descriptor))
                    for descid in descriptor.refs:
                        desc=s.heap.get(descid)
                        if desc and desc.kind=="instance" and any(not self.lookup_class(cls,"__get__",s).is_bottom() for cls in desc.class_ids):
                            for method in self.getattr(Value.ref(descid),"__get__",s.copy(),frame,node):
                                if method.kind=="normal":result.extend(self.invoke(method.value,[Value.ref(ref),Value(refs=obj.class_ids)],(),method.store,frame,node))
                                else:result.append(method)
                        else:
                            bound_value=self.bound(Value.ref(descid),Value.ref(ref),s,frame,node)
                            result.append(Flow(s.copy(),value=bound_value))
                else:
                    fallback=self.ops.join_all(self.lookup_class(cls,"__getattr__",s) for cls in obj.class_ids)
                    if not fallback.is_bottom():result.extend(self.invoke(self.bound(fallback,Value.ref(ref),s,frame,node),[Value.constant(name)],(),s,frame,node))
                    else:result.append(self.error(s,"AttributeError"))
        if not base.refs and not base.top:
            constants=base.exact_constants()
            if name in {"real","imag","numerator","denominator"} and constants is not None and all(type(x) in {int,bool,float} for x in constants):
                values=[]
                for x in constants:
                    if name in {"numerator","denominator"} and type(x) is float:continue
                    values.append(Value.constant(getattr(x,name)))
                if values:result.append(Flow(store.copy(),value=self.ops.join_all(values)))
                else:result.append(self.error(store.copy(),"AttributeError"))
            elif name in {"upper","lower","startswith","endswith","strip","split","join","format","replace"}:
                s=store.copy();identifier=f"scalar-method:{self.d.point(node)}:{name}";s.heap[identifier]=HeapObject("builtin_method",metadata={"name":name,"receiver":base});result.append(Flow(s,value=Value.ref(identifier)))
            elif constants is not None and any(known_builtin_attribute(type(x).__name__,name) for x in constants):result.extend(self.unknown(store,node,"unmodeled_scalar_attribute:"+name,havoc=False))
            else:result.append(self.error(store.copy(),"AttributeError"))
        return result

    def setattr(self,base,name,value,store,frame,node):
        result=[]
        if base.top or base.top_sorts:result.extend(self.unknown(store,node,"unknown_attribute_write:"+name))
        for ref in base.refs:
            s=store.copy();obj=s.heap.get(ref)
            if obj is None:result.extend(self.unknown(s,node,"unknown_attribute_receiver"));continue
            if obj.kind in {"list","dict","tuple","set","range","iterator","generator","coroutine"}:
                result.append(self.error(s,"AttributeError"));continue
            if obj.kind=="function" and name in {"__defaults__","__kwdefaults__","__code__","__closure__"}:
                self.d.open("function_metadata_mutation:"+name,s,node);obj.havoc=True
                for key in obj.fields:
                    if str(key).startswith(("$default:","$kwdefault:")):obj.fields[key]=Value.unknown();s.writes.add(("heap",ref,key))
            if obj.kind=="instance":
                custom=self.ops.join_all(self.lookup_class(cls,"__setattr__",s) for cls in obj.class_ids)
                if not custom.is_bottom():
                    result.extend(self.invoke(self.bound(custom,Value.ref(ref),s,frame,node),[Value.constant(name),value],(),s,frame,node));continue
            descriptors=self.ops.join_all(self.lookup_class(cls,name,s) for cls in obj.class_ids)
            handled=False
            for descid in descriptors.refs:
                desc=s.heap.get(descid)
                if desc and desc.kind=="property":
                    handled=True;setter=desc.fields.get("fset",Value.bottom())
                    if setter.is_bottom():result.append(self.error(s.copy(),"AttributeError"))
                    else:result.extend(self.invoke(setter,[Value.ref(ref),value],(),s.copy(),frame,node))
                elif desc:
                    setters=self.ops.join_all(self.lookup_class(cls,"__set__",s) for cls in desc.class_ids)
                    if not setters.is_bottom():handled=True;result.extend(self.invoke(self.bound(setters,Value.ref(descid),s,frame,node),[Value.ref(ref),value],(),s.copy(),frame,node))
            if not handled:
                if obj.kind=="module":s.write_cell(f"global:{obj.metadata['module']}:{name}",value)
                else:s.write_slot(ref,name,value,self.ops,weak=len(base.refs)>1)
                if self.d.oracle is not None and hasattr(self.d.oracle,"publish_field") and obj.kind!="module":self.d.oracle.publish_field(ref,name,s.heap[ref].fields[name],"ai_attribute_write")
                self.d.writes.add((ref,name,self.d.point(node)));result.append(self.normal(s))
        return result

    def getitem(self,base,key,store,frame,node):
        result=[];constants=key.exact_constants()
        if base.top:result.extend(self.unknown(store,node,"unknown_subscript",havoc=False))
        for ref in base.refs:
            obj=store.heap.get(ref)
            if obj is None:result.extend(self.unknown(store,node,"unknown_subscript_receiver",havoc=False));continue
            if obj.kind not in ("dict","list","tuple","range","set"):
                for method in self.getattr(Value.ref(ref),"__getitem__",store.copy(),frame,node):
                    if method.kind=="normal":result.extend(self.invoke(method.value,[key],(),method.store,frame,node))
                    else:result.append(method)
                continue
            if obj.kind=="dict" and obj.default.is_bottom() and not obj.havoc:
                lookup_keys=list(obj.fields) if constants is None else list(constants)
                partitioned=bool(self.d.abstract.get("enable_value_partition",False))
                cap=int(self.d.abstract.get("max_value_partitions",4))
                all_handlers={handler for candidate in lookup_keys for handler in obj.fields.get(candidate,Value.bottom()).refs}
                if partitioned:
                    for concrete in lookup_keys:
                        value=obj.fields.get(concrete,Value.bottom())
                        if value.is_bottom():continue
                        s=store.copy()
                        if isinstance(node,ast.Subscript) and isinstance(node.slice,ast.Name):s.cells[frame.cell(node.slice.id)]=Value.constant(concrete)
                        tokens=KeyValueRelation(frozenset(PartitionToken.make(self.d.point(node),concrete,self.d.functions[handler].qualname if handler in self.d.functions else handler,True) for handler in value.refs)).coarsen(cap)
                        result.append(Flow(s,value=replace(value,relations=value.relations|tokens.tokens)))
                else:
                    value=self.ops.join_all(obj.fields.get(concrete,Value.bottom()) for concrete in lookup_keys)
                    tokens=frozenset(PartitionToken.make(self.d.point(node),concrete,self.d.functions[handler].qualname if handler in self.d.functions else handler,False) for concrete in lookup_keys for handler in all_handlers)
                    if not value.is_bottom():result.append(Flow(store.copy(),value=replace(value,relations=value.relations|tokens)))
                if constants is None or any(concrete not in obj.fields or concrete in obj.maybe_missing for concrete in lookup_keys):result.append(self.error(store.copy(),"KeyError"))
                self.d.stats["value_partition_reads" if partitioned else "unpartitioned_key_reads"]+=1
                continue
            if constants is None:
                value=self.ops.join_all((*obj.fields.values(),obj.default))
                if obj.havoc:value=self.ops.join(value,Value.unknown())
                if not value.is_bottom():result.append(Flow(store.copy(),value=value))
                result.append(self.error(store.copy(),"KeyError" if obj.kind=="dict" else "IndexError"))
            else:
                for concrete in constants:
                    s=store.copy();actual=concrete
                    if obj.kind!="dict" and type(actual) in (int,bool) and actual<0 and "length" in obj.metadata:actual+=obj.metadata["length"]
                    value,missing=s.read_slot(ref,actual,self.ops)
                    if not value.is_bottom():result.append(Flow(s.copy(),value=value))
                    if missing:result.append(self.error(s,"KeyError" if obj.kind=="dict" else "IndexError"))
                    self.d.reads.add((ref,str(actual),self.d.point(node)))
        if not base.refs and not base.top:
            strings=base.strings.get_consts()
            if strings is not None and constants is not None:
                values=[]
                for s,k in product(strings,constants):
                    try:values.append(Value.constant(s[k]))
                    except (IndexError,TypeError):result.append(self.error(store.copy(),"IndexError"))
                if values:result.append(Flow(store.copy(),value=self.ops.join_all(values)))
        return result

    def setitem(self,base,key,value,store,frame,node):
        result=[];constants=key.exact_constants()
        if base.top:result.extend(self.unknown(store,node,"unknown_subscript_write"))
        for ref in base.refs:
            s=store.copy();obj=s.heap.get(ref)
            if obj is None:result.extend(self.unknown(s,node,"unknown_subscript_receiver"));continue
            if obj.kind not in ("dict","list"):
                for method in self.getattr(Value.ref(ref),"__setitem__",s,frame,node):
                    if method.kind=="normal":result.extend(self.invoke(method.value,[key,value],(),method.store,frame,node))
                    else:result.append(method)
                continue
            if constants is None:
                obj.default=self.ops.join(obj.default,value)
                for k in obj.fields:obj.fields[k]=self.ops.join(obj.fields[k],value);s.writes.add(("heap",ref,k))
                s.writes.add(("heap",ref,None));obj.metadata.pop("length",None)
            else:
                valid=False
                for concrete in constants:
                    actual=concrete
                    if obj.kind=="list":
                        if type(actual) not in {int,bool}:result.append(self.error(s.copy(),"TypeError"));continue
                        length=obj.metadata.get("length")
                        if length is not None:
                            if actual<0:actual+=length
                            if actual<0 or actual>=length:result.append(self.error(s.copy(),"IndexError"));continue
                        else:result.append(self.error(s.copy(),"IndexError"))
                    valid=True;s.write_slot(ref,actual,value,self.ops,weak=len(base.refs)>1 or len(constants)>1)
                if not valid:continue
                if obj.kind=="dict":obj.metadata["length"]=len(obj.fields)
            if self.d.oracle is not None and hasattr(self.d.oracle,"publish_field"):
                for k,v in obj.fields.items():self.d.oracle.publish_field(ref,k,v,"ai_item_write")
            self.d.writes.add((ref,"*" if constants is None else repr(constants),self.d.point(node)));result.append(self.normal(s))
        return result

    def define_class(self,node,store,frame):
        if node.keywords:
            result=[]
            for flow in self.unknown(store,node,"class_keyword_or_metaclass_protocol"):
                if flow.kind=="normal":self.write_name(node.name,Value.unknown(),flow.store,frame,node)
                result.append(flow)
            return result
        states,errors=self.eval_nodes((*node.decorator_list,*node.bases),store,frame);result=list(errors)
        for s,values in states:
            decorators=values[:len(node.decorator_list)];basevals=values[len(node.decorator_list):]
            bases=tuple(ref for value in basevals for ref in value.refs if (ref in s.heap and s.heap[ref].kind=="class") or ref=="builtin:object" or ref.startswith("builtin:") and ref.split(":",1)[1] in EXCEPTION_NAMES)
            identifier=f"class:{self.d.point(node)}@{'|'.join(frame.context)}"
            local,global_names,nonlocal_names,_=classify_bindings(node)
            closure=dict(frame.closure)
            if not frame.is_module:closure.update((n,frame.cell(n)) for n in frame.locals)
            classframe=Frame(frame.module,frame.qualname+"."+node.name,frame.context,identifier,local,tuple(closure.items()),global_names,nonlocal_names,is_class=True)
            for out in self.block(node.body,[self.normal(s)],classframe):
                if out.kind!="normal":result.append(out);continue
                fields={name:out.store.cells.get(classframe.cell(name),Value.bottom()) for name in local}
                fields={k:v for k,v in fields.items() if not v.is_bottom()}
                created_class=HeapObject("class",fields,metadata={"name":node.name,"bases":bases,"mro":self.c3(bases,out.store),"qualname":classframe.qualname})
                if identifier in out.store.heap:
                    created_class=out.store.heap[identifier].join(created_class,self.ops);created_class.singleton=False
                out.store.heap[identifier]=created_class
                pending=[Flow(out.store,value=Value.ref(identifier))]
                # Descriptor initialization hooks execute before decorators.
                for name,value in fields.items():
                    for descid in value.refs:
                        desc=out.store.heap.get(descid)
                        hooks=self.ops.join_all(self.lookup_class(cls,"__set_name__",out.store) for cls in desc.class_ids) if desc else Value.bottom()
                        if not hooks.is_bottom():
                            next_pending=[]
                            for p in pending:
                                if p.kind!="normal":next_pending.append(p);continue
                                for h in self.invoke(self.bound(hooks,Value.ref(descid),p.store,frame,node),[Value.ref(identifier),Value.constant(name)],(),p.store,frame,node):
                                    next_pending.append(Flow(h.store,h.kind,Value.ref(identifier) if h.kind=="normal" else h.value,h.exception))
                            pending=next_pending
                for base in bases:
                    hook=self.lookup_class(base,"__init_subclass__",out.store)
                    if not hook.is_bottom():
                        next_pending=[]
                        for p in pending:
                            if p.kind!="normal":next_pending.append(p);continue
                            for h in self.invoke(hook,[Value.ref(identifier)],(),p.store,frame,node):next_pending.append(Flow(h.store,h.kind,Value.ref(identifier) if h.kind=="normal" else h.value,h.exception))
                        pending=next_pending
                for decorator in reversed(decorators):pending=[h for p in pending for h in (self.invoke(decorator,[p.value],(),p.store,frame,node) if p.kind=="normal" else [p])]
                for p in pending:
                    if p.kind=="normal":self.write_name(node.name,p.value,p.store,frame,node);result.append(self.normal(p.store))
                    else:result.append(p)
        return result

    def c3(self,bases,store):
        sequences=[]
        for base in bases:
            obj=store.heap.get(base)
            sequences.append([base,*list(obj.metadata.get("mro",()))] if obj else [base])
        sequences.append(list(bases));result=[]
        while any(sequences):
            sequences=[s for s in sequences if s];candidate=None
            for seq in sequences:
                if not any(seq[0] in other[1:] for other in sequences):candidate=seq[0];break
            if candidate is None:
                self.d.open("inconsistent_mro");return tuple(dict.fromkeys((*result,*bases)))
            result.append(candidate)
            for seq in sequences:
                if seq and seq[0]==candidate:seq.pop(0)
        return tuple(dict.fromkeys(result))

    def construct(self,clsid,args,keywords,store,frame,node):
        cls=store.heap[clsid];new=self.lookup_class(clsid,"__new__",store);result=[]
        if new.is_bottom():created=[Flow(store,value=self.alloc(node,store,frame,"instance",class_ids=(clsid,)))]
        else:created=self.invoke(new,[Value.ref(clsid),*args],keywords,store,frame,node)
        for made in created:
            if made.kind!="normal":result.append(made);continue
            tags=self.exception_class_tags(clsid,made.store)
            if "builtin:BaseException" in tags:
                for ref in made.value.refs:
                    if ref in made.store.heap:made.store.heap[ref].metadata.update({"exception_name":cls.metadata.get("name","BaseException"),"exception_types":tuple(tags)})
            initializer=self.lookup_class(clsid,"__init__",made.store)
            applicable=any(clsid in made.store.heap.get(r,HeapObject("object")).class_ids for r in made.value.refs)
            if initializer.is_bottom() or not applicable:result.append(made)
            else:
                for initialized in self.invoke(self.bound(initializer,made.value,made.store,frame,node),args,keywords,made.store,frame,node):
                    if initialized.kind=="normal":result.append(Flow(initialized.store,value=made.value))
                    else:result.append(initialized)
        return result

    def import_statement(self,node,store,frame):
        if isinstance(node,ast.ImportFrom) and node.module=="__future__":
            result=store.copy()
            for alias in node.names:self.write_name(alias.asname or alias.name,Value.ref("builtin:__future__."+alias.name),result,frame,node)
            return [self.normal(result)]
        result=[self.normal(store.copy())]
        if isinstance(node,ast.Import):
            for alias in node.names:
                next_result=[]
                for flow in result:
                    if flow.kind!="normal":next_result.append(flow);continue
                    for imported in self.d.request_module(alias.name,flow.store,self.d.current,node):
                        if imported.kind=="normal":
                            target=alias.asname or alias.name.split('.')[0]
                            if alias.asname is None and '.' in alias.name:
                                parent=alias.name.split('.')[0];value=self.d.register_module(parent,imported.store)
                            else:value=imported.value
                            self.write_name(target,value,imported.store,frame,node);next_result.append(self.normal(imported.store))
                        else:next_result.append(imported)
                result=next_result
            return result
        package=frame.module if self.d.modules[frame.module].is_package else frame.module.rpartition('.')[0]
        if node.level:
            pieces=package.split('.') if package else []
            if node.level>len(pieces)+1:return [self.error(store.copy(),"ImportError")]
            prefix='.'.join(pieces[:len(pieces)-node.level+1]);module='.'.join(x for x in (prefix,node.module or '') if x)
        else:module=node.module or ''
        imported=self.d.request_module(module,store,self.d.current,node);result=[]
        for flow in imported:
            if flow.kind!="normal":result.append(flow);continue
            paths=[flow]
            for alias in node.names:
                next_paths=[]
                for p in paths:
                    if p.kind!="normal":next_paths.append(p);continue
                    if alias.name=='*':
                        prefix=f"global:{module}:"
                        for cell,value in list(p.store.cells.items()):
                            if cell.startswith(prefix) and not cell[len(prefix):].startswith('_'):self.write_name(cell[len(prefix):],value,p.store,frame,node)
                        next_paths.append(p);continue
                    value=p.store.cells.get(f"global:{module}:{alias.name}",Value.bottom())
                    submodule=module+'.'+alias.name
                    if value.is_bottom() and submodule in self.d.modules:
                        candidates=self.d.request_module(submodule,p.store,self.d.current,node)
                        for sub in candidates:
                            if sub.kind=="normal":self.write_name(alias.asname or alias.name,sub.value,sub.store,frame,node);next_paths.append(self.normal(sub.store))
                            else:next_paths.append(sub)
                    elif value.is_bottom() and module not in self.d.modules:
                        self.d.open("external_import:"+module+'.'+alias.name,p.store,node);self.write_name(alias.asname or alias.name,Value.unknown(),p.store,frame,node);next_paths.append(self.normal(p.store))
                    elif value.is_bottom():next_paths.append(self.error(p.store.copy(),"ImportError"))
                    else:self.write_name(alias.asname or alias.name,value,p.store,frame,node);next_paths.append(self.normal(p.store))
                paths=next_paths
            result.extend(paths)
        return result

    def builtin(self,name,args,keywords,store,frame,node):
        if name in {"eval","exec","__import__"}:return self.unknown(store,node,"dynamic_code:"+name)
        if name in EXCEPTION_NAMES:
            ancestors=builtin_exception_ancestors(name)
            if ancestors & {"OSError","SyntaxError"} or name in {"UnicodeDecodeError","UnicodeEncodeError","UnicodeTranslateError","ExceptionGroup","BaseExceptionGroup"}:
                return self.unknown(store,node,"complex_exception_constructor:"+name)
            allowed={"AttributeError":{"name","obj"},"NameError":{"name"},"ImportError":{"name","path"},"ModuleNotFoundError":{"name","path"}}
            if any(key not in allowed.get(name,set()) for key,_ in keywords):return [self.error(store,"TypeError")]
            fields=dict(keywords);argtuple=self.alloc(node,store,frame,"tuple",{i:value for i,value in enumerate(args)},{"length":len(args)})
            fields["args"]=argtuple
            return [Flow(store,value=self.alloc(node,store,frame,"exception",fields,{"exception_name":canonical_builtin_exception_name(name),"exception_types":tuple(builtin_exception_ancestors(name))}))]
        if name=="print":self.d.events.append(("call","print"));return [self.normal(store)]
        if name in {"staticmethod","classmethod"}:
            if len(args)!=1:return [self.error(store,"TypeError")]
            return [Flow(store,value=self.alloc(node,store,frame,name,{"callable":args[0]}))]
        if name=="property":
            fields={key:value for key,value in zip(("fget","fset","fdel","doc"),args)};fields.update(dict(keywords))
            return [Flow(store,value=self.alloc(node,store,frame,"property",fields))]
        if name=="object":return [Flow(store,value=self.alloc(node,store,frame,"instance"))]
        if name in {"list","tuple","set","dict"}:
            if name=="dict":
                fields=dict(keywords)
                if args:
                    for ref in args[0].refs:
                        obj=store.heap.get(ref)
                        if obj and obj.kind=="dict":fields={**obj.fields,**fields}
                        else:return self.unknown(store,node,"dict_constructor_input",havoc=False)
                return [Flow(store,value=self.alloc(node,store,frame,name,fields,{"length":len(fields)}))]
            if not args:return [Flow(store,value=self.alloc(node,store,frame,name,{}, {"length":0}))]
            sequences,unknown=self.iterable_values(args[0],store);result=[]
            for sequence in sequences:result.append(Flow(store.copy(),value=self.alloc(node,store,frame,name,{i:v for i,v in enumerate(sequence)},{"length":len(sequence)})))
            if unknown:result.extend(self.unknown(store,node,"container_constructor_input",havoc=False))
            return result
        if name=="range":
            constants=[x.exact_constants() for x in args];result=[]
            if not args or len(args)>3:return [self.error(store,"TypeError")]
            if all(x is not None for x in constants):
                for combo in product(*constants):
                    try:
                        seq=range(*combo)
                        if len(seq)>self.d.abstract.get("max_range_size",1000):return self.unknown(store,node,"range_enumeration_budget",havoc=False)
                        s=store.copy();result.append(Flow(s,value=self.alloc(node,s,frame,"range",{i:Value.constant(x) for i,x in enumerate(seq)},{"length":len(seq)})))
                    except (TypeError,ValueError,OverflowError) as exc:result.append(self.error(store.copy(),type(exc).__name__))
                return result
            return self.unknown(store,node,"dynamic_range",havoc=False)
        if name=="len":
            if len(args)!=1:return [self.error(store,"TypeError")]
            values=[];unknown=args[0].top
            constants=args[0].exact_constants()
            if constants is not None:
                for x in constants:
                    try:values.append(Value.constant(len(x)))
                    except TypeError:pass
            for ref in args[0].refs:
                obj=store.heap.get(ref)
                if obj and "length" in obj.metadata and not obj.havoc:values.append(Value.constant(obj.metadata["length"]))
                elif obj and obj.kind=="dict" and obj.default.is_bottom():values.append(Value.constant(len(obj.fields)))
                else:unknown=True
            if unknown:values.append(Value(ints=self.ops.integer.top()))
            return [Flow(store,value=self.ops.join_all(values))] if values else [self.error(store,"TypeError")]
        if name in {"str","int","bool","float","repr","abs"}:
            if not args:return [Flow(store,value=Value.constant({"str":"","int":0,"bool":False,"float":0.0,"repr":"","abs":0}[name]))]
            if name=="bool":return self.truth_flows(args[0],store,frame,node)
            constants=args[0].exact_constants();function={"str":str,"int":int,"float":float,"repr":repr,"abs":abs}[name];values=[];result=[]
            if constants is not None:
                for x in constants:
                    try:values.append(Value.constant(function(x)))
                    except (TypeError,ValueError,OverflowError) as exc:result.append(self.error(store.copy(),type(exc).__name__))
                if values:result.append(Flow(store,value=self.ops.join_all(values)))
                return result
            sort="str" if name in {"str","repr"} else "int" if name in {"int","abs"} else "float"
            return [Flow(store,value=Value(strings=self.ops.string.top()) if sort=="str" else Value(ints=self.ops.integer.top()) if sort=="int" else Value(top_sorts=frozenset((sort,))))]
        if name in {"getattr","hasattr","setattr","delattr"}:
            if len(args)<2:return [self.error(store,"TypeError")]
            names=args[1].exact_constants();result=[]
            if names is None:return self.unknown(store,node,"dynamic_attribute_name",havoc=name in {"setattr","delattr"})
            for attrname in names:
                if type(attrname) is not str:result.append(self.error(store.copy(),"TypeError"));continue
                if name=="setattr":
                    if len(args)<3:result.append(self.error(store.copy(),"TypeError"))
                    else:result.extend(self.setattr(args[0],attrname,args[2],store.copy(),frame,node))
                elif name=="delattr":
                    s=store.copy()
                    for ref in args[0].refs:
                        if ref in s.heap:s.heap[ref].fields.pop(attrname,None);s.heap[ref].maybe_missing.add(attrname);s.writes.add(("heap",ref,attrname))
                    result.append(self.normal(s))
                else:
                    for out in self.getattr(args[0],attrname,store.copy(),frame,node):
                        if name=="hasattr":
                            if out.kind=="normal":result.append(Flow(out.store,value=Value.constant(True)))
                            elif out.exception=="AttributeError":result.append(Flow(out.store,value=Value.constant(False)))
                            else:result.append(out)
                        elif out.exception=="AttributeError" and len(args)>2:result.append(Flow(out.store,value=args[2]))
                        else:result.append(out)
            return result
        if name in {"isinstance","issubclass"}:
            if len(args)!=2:return [self.error(store,"TypeError")]
            truths=set()
            for ref in args[0].refs:
                obj=store.heap.get(ref);classes=obj.class_ids if obj and name=="isinstance" else frozenset((ref,))
                for cls in args[1].refs:truths.add(cls in classes or any(cls in store.heap.get(c,HeapObject("class")).metadata.get("mro",()) for c in classes))
            if args[0].top or not truths:truths.update((False,True))
            return [Flow(store,value=Value(bools=frozenset(truths)))]
        if name=="type":
            if len(args)==1:
                refs=set()
                for ref in args[0].refs:
                    obj=store.heap.get(ref)
                    if obj:refs|=set(obj.class_ids)
                return [Flow(store,value=Value(refs=frozenset(refs)) if refs else Value.unknown())]
            return self.unknown(store,node,"dynamic_class_construction")
        if name=="iter":
            if not args:return [self.error(store,"TypeError")]
            return [Flow(store,value=self.alloc(node,store,frame,"iterator",metadata={"container":args[0],"index":0}))]
        if name=="next":
            if not args:return [self.error(store,"TypeError")]
            result=self.resume(args[0],store,frame,node,"next")
            if len(args)>1:result=[Flow(f.store,value=args[1]) if f.exception=="StopIteration" else f for f in result]
            return result
        if name in {"sum","all","any","min","max","sorted","reversed","enumerate","zip"}:return self.aggregate_builtin(name,args,store,frame,node)
        if name=="super":
            if len(args)<2:
                classcell=dict(frame.closure).get("__class__");owner=store.cells.get(classcell,Value.bottom()) if classcell else Value.bottom()
                function=next((info for info in self.d.functions.values() if info.qualname==frame.qualname),None)
                positional=(*function.node.args.posonlyargs,*function.node.args.args) if function else ()
                receiver=store.cells.get(frame.cell(positional[0].arg),Value.bottom()) if positional else Value.bottom()
                if owner.is_bottom() or receiver.is_bottom():return [self.error(store,"RuntimeError")]
                args=[owner,receiver]
            return [Flow(store,value=self.alloc(node,store,frame,"super",metadata={"class":args[0],"receiver":args[1]}))]
        return self.unknown(store,node,"unsupported_builtin:"+name)

    def builtin_method(self,name,receiver,args,keywords,store,frame,node):
        result=[]
        if name in {"send","throw","close","__next__","__await__"}:
            return self.resume(receiver,store,frame,node,{"__next__":"next","__await__":"await"}.get(name,name),args[0] if args else Value.constant(None))
        if name=="__iter__":return self.builtin("iter",[receiver],(),store,frame,node)
        for ref in receiver.refs:
            obj=store.heap.get(ref)
            if obj is None:continue
            s=store.copy();target=s.heap[ref]
            if obj.havoc:
                result.extend(self.unknown(s,node,"havoc_builtin_state"));continue
            if obj.kind=="property" and name in {"setter","getter","deleter"}:
                if not args:result.append(self.error(s,"TypeError"));continue
                fields=dict(target.fields);fields[{"setter":"fset","getter":"fget","deleter":"fdel"}[name]]=args[0]
                result.append(Flow(s,value=self.alloc(node,s,frame,"property",fields)));continue
            if obj.kind=="list" and name in {"append","extend","insert"}:
                length=target.metadata.get("length")
                if name=="append" and len(args)==1:
                    if length is None:target.default=self.ops.join(target.default,args[0]);s.writes.add(("heap",ref,None))
                    else:s.write_slot(ref,length,args[0],self.ops);target.metadata["length"]=length+1
                    result.append(self.normal(s));continue
                if name=="extend" and args:
                    sequences,unknown=self.iterable_values(args[0],s)
                    for sequence in sequences:
                        copy=s.copy();t=copy.heap[ref];n=t.metadata.get("length")
                        if n is not None:
                            for index,value in enumerate(sequence):copy.write_slot(ref,n+index,value,self.ops)
                            t.metadata["length"]=n+len(sequence)
                        else:t.default=self.ops.join(t.default,self.ops.join_all(sequence));copy.writes.add(("heap",ref,None))
                        result.append(self.normal(copy))
                    if unknown:result.extend(self.unknown(s,node,"unknown_list_extend"))
                    continue
            if obj.kind=="dict":
                if name=="get" and args:
                    default=args[1] if len(args)>1 else Value.constant(None)
                    for flow in self.getitem(receiver,args[0],s,frame,node):
                        result.append(Flow(flow.store,value=default) if flow.exception=="KeyError" else flow)
                    continue
                if name=="setdefault" and args:
                    default=args[1] if len(args)>1 else Value.constant(None)
                    for flow in self.getitem(receiver,args[0],s,frame,node):
                        if flow.exception=="KeyError":
                            for stored in self.setitem(receiver,args[0],default,flow.store,frame,node):result.append(Flow(stored.store,stored.kind,default if stored.kind=="normal" else stored.value,stored.exception))
                        else:result.append(flow)
                    continue
                if name=="update":
                    fields=dict(keywords)
                    if args:
                        for other in args[0].refs:
                            rhs=s.heap.get(other)
                            if rhs and rhs.kind=="dict":fields.update(rhs.fields)
                            else:self.d.open("unknown_dict_update",s,node);target.havoc=True
                    for key,value in fields.items():s.write_slot(ref,key,value,self.ops)
                    target.metadata["length"]=len(target.fields);result.append(self.normal(s));continue
                if name in {"keys","values","items"}:
                    values=[]
                    for key,value in target.fields.items():
                        if name=="keys":values.append(Value.constant(key))
                        elif name=="values":values.append(value)
                        else:values.append(self.alloc(node,s,frame,"tuple",{0:Value.constant(key),1:value},{"length":2}))
                    result.append(Flow(s,value=self.alloc(node,s,frame,"list",{i:v for i,v in enumerate(values)},{"length":len(values)})));continue
            if name=="clear" and obj.kind in {"dict","list","set"}:
                for key in list(target.fields):target.fields.pop(key,None);target.maybe_missing.add(key);s.writes.add(("heap",ref,key))
                target.default=Value.bottom();target.metadata["length"]=0;s.writes.add(("heap",ref,None));result.append(self.normal(s));continue
            if name=="copy" and obj.kind in {"dict","list","set"}:
                value=self.alloc(node,s,frame,obj.kind,dict(target.fields),dict(target.metadata));result.append(Flow(s,value=value));continue
            if name=="pop" and obj.kind in {"dict","list"}:
                keys=args[0].exact_constants() if args else [target.metadata.get("length",1)-1]
                if keys is None:result.extend(self.unknown(s,node,"unknown_pop"));continue
                for key in keys:
                    value,missing=s.read_slot(ref,key,self.ops)
                    if not value.is_bottom():
                        copy=s.copy();copy.heap[ref].fields.pop(key,None);copy.heap[ref].maybe_missing.add(key);copy.writes.add(("heap",ref,key));result.append(Flow(copy,value=value))
                    if missing:result.append(Flow(s.copy(),value=args[1]) if len(args)>1 else self.error(s.copy(),"KeyError" if obj.kind=="dict" else "IndexError"))
                continue
        if result:return result
        constants=receiver.exact_constants();arg_constants=[arg.exact_constants() for arg in args]
        if constants is not None and all(x is not None for x in arg_constants):
            for value in constants:
                if type(value) is not str:continue
                method=getattr(str,name,None)
                if method is None:continue
                for combo in product(*arg_constants):
                    try:
                        concrete=method(value,*combo,**{key:val.exact_constants()[0] for key,val in keywords})
                        if type(concrete) is list:
                            s=store.copy();out=self.alloc(node,s,frame,"list",{i:Value.constant(x) for i,x in enumerate(concrete)},{"length":len(concrete)});result.append(Flow(s,value=out))
                        else:result.append(Flow(store.copy(),value=Value.constant(concrete)))
                    except (TypeError,ValueError,IndexError) as exc:result.append(self.error(store.copy(),type(exc).__name__))
        return result or self.unknown(store,node,"unsupported_builtin_method:"+name)

    def resume(self,receiver,store,frame,node,operation,value=None):
        result=[];value=Value.constant(None) if value is None else value
        for ref in receiver.refs:
            obj=store.heap.get(ref)
            if obj is None:result.extend(self.unknown(store,node,"unknown_resume"));continue
            s=store.copy();current=s.heap[ref]
            if obj.havoc:
                result.extend(self.unknown(s,node,"havoc_suspended_or_iterator_state"));continue
            if obj.kind=="iterator":
                container=current.metadata["container"];index=current.metadata.get("index",0);values=[];missing=False
                for base in container.refs:
                    sequence=s.heap.get(base)
                    if sequence and sequence.kind in {"list","tuple","range"}:
                        if index in sequence.fields:values.append(sequence.fields[index])
                        else:missing=True
                    elif sequence and sequence.kind=="dict":
                        keys=list(sequence.fields)
                        if index<len(keys):values.append(Value.constant(keys[index]))
                        else:missing=True
                    else:self.d.open("dynamic_iterator",s,node);values.append(Value.unknown());missing=True
                if values:
                    current.metadata["index"]=index+1;s.writes.add(("heap",ref,"<iterator-state>"));result.append(Flow(s.copy(),value=self.ops.join_all(values)))
                if missing or not values:result.append(self.error(s,"StopIteration"))
                continue
            if obj.kind not in {"generator","coroutine"}:result.append(self.error(s,"TypeError"));continue
            stage=current.metadata.get("stage","created")
            if stage=="closed":
                result.append(self.normal(s) if operation=="close" else self.error(s,"StopIteration"));continue
            generator_frame=current.metadata["frame"]
            for cell,initial in current.metadata["entry"].cells.items():
                if cell.startswith("local:"+generator_frame.identity+":") and cell not in s.cells:s.cells[cell]=initial
            cleanups=current.metadata.get("cleanups",())
            if operation in {"close","throw"}:
                pending=[self.normal(s)]
                if stage!="created":
                    for cleanup in cleanups:pending=self.block(cleanup,pending,generator_frame)
                for out in pending:
                    out.store.heap[ref].metadata["stage"]="closed";out.store.writes.add(("heap",ref,"<generator-state>"))
                    if out.kind not in {"normal","return"}:result.append(self.error(out.store,"RuntimeError") if out.kind=="suspend" else out)
                    elif operation=="close":result.append(self.normal(out.store))
                    else:
                        names=set()
                        for exception_ref in value.refs:
                            if exception_ref.startswith("builtin:"):names.add(exception_ref.split(':',1)[1])
                            target=out.store.heap.get(exception_ref)
                            if target:names.update(target.metadata.get("exception_types",()))
                        result.extend(self.error(out.store.copy(),name) for name in names or {"BaseException"})
                continue
            if stage=="created" and operation=="send" and value.exact_constants()!=[None]:result.append(self.error(s,"TypeError"));continue
            if current.metadata.get("resume_target") is not None:
                assigned=self.assign(current.metadata["resume_target"],value,s,generator_frame,node)
                starting=[flow for flow in assigned if flow.kind=="normal"]
                result.extend(flow for flow in assigned if flow.kind!="normal")
            else:starting=[self.normal(s)]
            body=current.metadata.get("remaining",current.metadata["body"])
            flows=self.block(body,starting,generator_frame)
            for out in flows:
                target=out.store.heap[ref]
                if out.kind=="suspend":
                    target.metadata.update({"stage":"suspended","remaining":out.continuation,"cleanups":(*out.cleanups,*cleanups),"resume_target":out.resume_target})
                    out.store.writes.add(("heap",ref,"<generator-state>"));result.append(Flow(out.store,value=out.value))
                elif out.kind in {"normal","return"}:
                    target.metadata["stage"]="closed";out.store.writes.add(("heap",ref,"<generator-state>"))
                    pending=[out]
                    for cleanup in cleanups:
                        pending=[resume_pending(p,self_cleanup) for p in pending for self_cleanup in self.block(cleanup,[self.normal(p.store)],generator_frame)]
                    for final in pending:
                        if operation=="await":result.append(Flow(final.store,value=final.value if final.kind=="return" else Value.constant(None)))
                        else:result.append(self.error(final.store,"StopIteration"))
                else:target.metadata["stage"]="closed";result.append(out)
        if receiver.top:result.extend(self.unknown(store,node,"unknown_resume"))
        return result

    def aggregate_builtin(self,name,args,store,frame,node):
        if not args:return [self.error(store,"TypeError")]
        sequences,unknown=self.iterable_values(args[0],store);result=[]
        for sequence in sequences:
            if name=="enumerate":
                start=0
                if len(args)>1:
                    constants=args[1].exact_constants();start=constants[0] if constants else 0
                s=store.copy();values=[self.alloc(node,s,frame,"tuple",{0:Value.constant(i+start),1:v},{"length":2}) for i,v in enumerate(sequence)]
                result.append(Flow(s,value=self.alloc(node,s,frame,"list",{i:v for i,v in enumerate(values)},{"length":len(values)})));continue
            if name in {"sorted","reversed"}:
                constants=[v.exact_constants() for v in sequence]
                if all(x is not None and len(x)==1 for x in constants):
                    scalars=[x[0] for x in constants]
                    try:values=[Value.constant(x) for x in (sorted(scalars) if name=="sorted" else list(reversed(scalars)))]
                    except TypeError:result.append(self.error(store.copy(),"TypeError"));continue
                    s=store.copy();result.append(Flow(s,value=self.alloc(node,s,frame,"list",{i:v for i,v in enumerate(values)},{"length":len(values)})));continue
            constants=[v.exact_constants() for v in sequence]
            if all(x is not None and len(x)==1 for x in constants):
                scalars=[x[0] for x in constants]
                try:
                    function={"sum":sum,"all":all,"any":any,"min":min,"max":max}.get(name)
                    if function:result.append(Flow(store.copy(),value=Value.constant(function(scalars))));continue
                except (TypeError,ValueError) as exc:result.append(self.error(store.copy(),type(exc).__name__));continue
            result.extend(self.unknown(store,node,"aggregate_input:"+name,havoc=False))
        if unknown:result.extend(self.unknown(store,node,"aggregate_iteration:"+name,havoc=False))
        return result

    def with_statement(self,node,store,frame):
        paths=[(store.copy(),[])];errors=[]
        for item in node.items:
            next_paths=[]
            for s,exits in paths:
                for manager in self.expr(item.context_expr,s,frame):
                    if manager.kind!="normal":errors.append(manager);continue
                    for exit_method in self.getattr(manager.value,"__exit__",manager.store,frame,node):
                        if exit_method.kind!="normal":errors.append(exit_method);continue
                        for enter in self.getattr(manager.value,"__enter__",exit_method.store,frame,node):
                            if enter.kind!="normal":errors.append(enter);continue
                            for entered in self.invoke(enter.value,[],(),enter.store,frame,node):
                                if entered.kind!="normal":errors.append(entered);continue
                                assigned=self.assign(item.optional_vars,entered.value,entered.store,frame,node) if item.optional_vars else [self.normal(entered.store)]
                                for assigned_flow in assigned:
                                    if assigned_flow.kind=="normal":next_paths.append((assigned_flow.store,[*exits,exit_method.value]))
                                    else:errors.append(assigned_flow)
            paths=next_paths
        result=list(errors)
        for s,exits in paths:
            pending=self.block(node.body,[self.normal(s)],frame)
            for exit_method in reversed(exits):
                cleaned=[]
                for flow in pending:
                    parameters=[Value.constant(flow.exception),flow.value,Value.constant(None)] if flow.kind=="raise" else [Value.constant(None)]*3
                    for cleanup in self.invoke(exit_method,parameters,(),flow.store,frame,node):
                        if cleanup.kind!="normal":cleaned.append(cleanup)
                        elif flow.kind=="raise":
                            for tested in self.truth_flows(cleanup.value,cleanup.store,frame,node):
                                if tested.kind!="normal":cleaned.append(tested)
                                else:
                                    if True in tested.value.bools:cleaned.append(self.normal(tested.store.copy()))
                                    if False in tested.value.bools:cleaned.append(replace(flow,store=tested.store))
                        else:cleaned.append(replace(flow,store=cleanup.store))
                pending=cleaned
            result.extend(pending)
        return result

    def comprehension(self,node,store,frame):
        paths=[self.normal(store.copy())]
        # Comprehension variables have a distinct lexical environment.
        local=frozenset(n.id for generator in node.generators for n in ast.walk(generator.target) if isinstance(n,ast.Name))
        identity=f"comprehension:{self.d.point(node)}@{'|'.join(frame.context)}";closure=dict(frame.closure)
        closure.update((name,frame.cell(name)) for name in frame.locals)
        compframe=Frame(frame.module,frame.qualname+".<comprehension>",frame.context,identity,local,tuple(closure.items()))
        for generator in node.generators:
            expanded=[]
            for p in paths:
                for iterable in self.expr(generator.iter,p.store,frame):
                    if iterable.kind!="normal":expanded.append(iterable);continue
                    sequences,unknown=self.iterable_values(iterable.value,iterable.store)
                    if unknown:self.d.open("comprehension_iteration",iterable.store,node)
                    values=[v for sequence in sequences for v in sequence]
                    if unknown:values.append(Value.unknown())
                    for value in values:
                        for assigned in self.assign(generator.target,value,iterable.store.copy(),compframe,node):
                            if assigned.kind!="normal":expanded.append(assigned);continue
                            possible=[assigned]
                            for condition in generator.ifs:
                                next_possible=[]
                                for candidate in possible:
                                    for tested in self.expr(condition,candidate.store,compframe):
                                        if tested.kind!="normal":next_possible.append(tested)
                                        else:
                                            for truth_flow in self.truth_flows(tested.value,tested.store,compframe,node):
                                                if truth_flow.kind!="normal":next_possible.append(truth_flow)
                                                elif True in truth_flow.value.bools:next_possible.append(self.normal(truth_flow.store))
                                possible=next_possible
                            expanded.extend(possible)
            paths=expanded
        errors=[];values=[];fields={};last=store.copy()
        for p in paths:
            if p.kind!="normal":errors.append(p);continue
            if isinstance(node,ast.DictComp):
                states,ex=self.eval_nodes((node.key,node.value),p.store,compframe);errors.extend(ex)
                for s,(key,value) in states:
                    constants=key.exact_constants()
                    if constants is None:self.d.open("comprehension_key",s,node)
                    else:
                        for concrete in constants:fields[concrete]=self.ops.join(fields.get(concrete,Value.bottom()),value)
                    last=last.join(s,self.ops)
            else:
                for out in self.expr(node.elt,p.store,compframe):
                    if out.kind=="normal":values.append(out.value);last=last.join(out.store,self.ops)
                    else:errors.append(out)
        kind="dict" if isinstance(node,ast.DictComp) else "set" if isinstance(node,ast.SetComp) else "list"
        if not isinstance(node,ast.DictComp):fields={i:v for i,v in enumerate(values)}
        ref=self.alloc(node,last,frame,kind,fields,{"length":len(fields)})
        return errors+[Flow(last,value=ref)]
