"""Finite straight-line Andersen region execution over a full boundary store.

This is a supported subset engine, not a renamed abstract transfer: source
expressions become allocation/copy/load/store inclusion constraints, solved by
the same incremental delta/replay kernel as the optional candidate oracle.
Opaque identities and complete boundary state are supplied by adapters.
"""
from __future__ import annotations
import ast
from dataclasses import replace
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.composition.oracle import IncrementalInclusionStore
from pythonstan.analysis.composition.state_adapter import FullStateAdapter,BoundaryObject
from pythonstan.config import ConfigurationError
from pythonstan.models.builtin_attributes import known_builtin_attribute
from pythonstan.models.builtins import PYTHON312_BUILTIN_NAMES

_ALLOWED_EXPRESSIONS=(ast.Constant,ast.Name,ast.List,ast.Tuple,ast.Dict,ast.Attribute,ast.Subscript,ast.BinOp,ast.UnaryOp)
_ALLOWED_STATEMENTS=(ast.Assign,ast.AnnAssign,ast.AugAssign,ast.Expr,ast.Return,ast.Pass,ast.Global,ast.Nonlocal)

def validate_native_statement(node):
    if isinstance(node,ast.Assign) and len(node.targets)>1:raise ConfigurationError("native-straightline-v1 excludes multi-target assignment prefix effects")
    if not isinstance(node,_ALLOWED_STATEMENTS):raise ConfigurationError("native-straightline-v1 does not support selected "+type(node).__name__)
    for expression in ast.walk(node):
        if isinstance(expression,ast.expr) and not isinstance(expression,_ALLOWED_EXPRESSIONS):raise ConfigurationError("native-straightline-v1 does not support selected expression "+type(expression).__name__)
        if isinstance(expression,ast.Dict) and any(key is None for key in expression.keys):raise ConfigurationError("native-straightline-v1 rejects **mapping literals")
    return True


def validate_region_plan(driver,plan):
    for tree in driver.trees.values():
        for node in ast.walk(tree):
            if not isinstance(node,ast.stmt):continue
            decision=plan.decisions.get(driver.point(node))
            if decision and decision["engine"]=="native" and decision.get("origin")!="default":validate_native_statement(node)
    return True


class NativeRegionEngine:
    def __init__(self):self.adapter=FullStateAdapter();self.runs=0;self.work=0;self.trace=[]
    def execute(self,node,flow,frame,driver):
        validate_native_statement(node)
        if flow.kind!="normal":return [flow]
        if not self.adapter.check_roundtrip(flow,driver.ops):raise RuntimeError("full-store boundary roundtrip lost state")
        boundary=self.adapter.encode(flow);kernel=IncrementalInclusionStore(driver.ops);temporaries=0;allocations={};cell_outputs={};field_writes=[];loads=[];exceptions=set();completion="normal";result_node=None;normal_possible=True
        for cell,value in boundary.cells.items():kernel.add_fact(("entry",cell),value,"ai_boundary_cell")
        for objid,obj in boundary.heap.items():
            for key,value in obj.fields.items():kernel.add_fact(("field",objid,key),value,"ai_boundary_heap")
            kernel.add_fact(("default",objid),obj.default,"ai_boundary_default")
        def temp():
            nonlocal temporaries
            temporaries+=1;return ("temporary",temporaries)
        def settle():
            remaining=max(0,driver.max_work-driver.work);before=kernel.work
            try:closed=kernel.solve(remaining)
            finally:driver.charge(kernel.work-before,"native_region_work")
            if not closed:
                from pythonstan.analysis.engines.abstract.driver import BudgetStop
                raise BudgetStop("native_region_global_work")
        def known(node_id):settle();return kernel.value(node_id)
        def direct(ref,key,write=False):
            obj=boundary.heap.get(ref) or allocations.get(ref)
            if obj is None:raise ConfigurationError("Native region boundary has no object declaration")
            if obj.havoc:raise ConfigurationError("Native region requires a closed direct-object boundary")
            if obj.kind=="class":raise ConfigurationError("Native direct-field profile excludes class/metaclass/descriptor lookup")
            if obj.kind not in {"dict","list","tuple","instance","class","set"}:raise ConfigurationError("Native region does not support "+obj.kind+" object protocol")
            if obj.class_ids:
                visited=set();classes=list(obj.class_ids)
                while classes:
                    clsid=classes.pop()
                    if clsid in visited:continue
                    visited.add(clsid)
                    if clsid=="builtin:object":continue
                    cls=boundary.heap.get(clsid)
                    if cls is None or cls.havoc:raise ConfigurationError("Native region class boundary is open")
                    if any(name in cls.fields for name in ("__getattribute__","__getattr__","__setattr__","__getitem__","__setitem__","__bool__","__len__")):raise ConfigurationError("Native region cannot bypass inherited user protocol methods")
                    if key in cls.fields:raise ConfigurationError("Native region requires own direct fields rather than class/descriptor lookup")
                    classes.extend(cls.metadata.get("mro",cls.metadata.get("bases",())))
            return obj
        def compile_expression(expr):
            target=temp()
            if isinstance(expr,ast.Constant):kernel.add_fact(target,Value.constant(expr.value),"literal");return target
            if isinstance(expr,ast.Name):
                source=("entry",frame.cell(expr.id));value=kernel.facts.get(source,Value(top=True,unbound=True) if frame.module in boundary.havoc_namespaces and frame.cell(expr.id).startswith("global:") else Value(unbound=True))
                if source not in kernel.facts:kernel.add_fact(source,value,"namespace_default_bound")
                can_fallback=frame.is_module or expr.id not in frame.locals and expr.id not in dict(frame.closure) and expr.id not in frame.nonlocals
                if value.unbound and can_fallback:
                    if expr.id in {"__name__","__package__","__file__"}:
                        module=driver.modules[frame.module]
                        item=frame.module if expr.id=="__name__" else module.filename if expr.id=="__file__" else frame.module if module.is_package else frame.module.rpartition('.')[0]
                        value=driver.ops.join(value.without_unbound(),Value.constant(item))
                    elif expr.id in PYTHON312_BUILTIN_NAMES:value=driver.ops.join(value.without_unbound(),Value.ref("builtin:"+expr.id))
                    if not value.unbound:kernel.add_fact(target,value,"builtin_name_lookup");return target
                if value.unbound:exceptions.add("UnboundLocalError" if expr.id in frame.locals else "NameError")
                kernel.add_edge(source,target);return target
            if isinstance(expr,(ast.List,ast.Tuple,ast.Dict)):
                kind=type(expr).__name__.lower();objid=f"{kind}:{driver.point(expr)}@{'|'.join(frame.context)}"
                old=boundary.heap.get(objid);obj=BoundaryObject(kind,{},Value.bottom(),set(),old is None,frozenset(),{},False)
                if isinstance(expr,ast.Dict):
                    for keyexpr,valueexpr in zip(expr.keys,expr.values):
                        keynode=compile_expression(keyexpr);valuenode=compile_expression(valueexpr);keys=known(keynode).exact_constants()
                        if keys is None:raise ConfigurationError("Native region literal requires finite builtin keys")
                        for key in keys:kernel.add_edge(valuenode,("field",objid,key));obj.fields[key]=Value.bottom()
                else:
                    for index,item in enumerate(expr.elts):kernel.add_edge(compile_expression(item),("field",objid,index));obj.fields[index]=Value.bottom()
                obj.metadata["length"]=len(obj.fields);allocations[objid]=obj
                kernel.add_fact(target,Value.ref(objid),"allocation");return target
            if isinstance(expr,(ast.Attribute,ast.Subscript)):
                basenode=compile_expression(expr.value);base=known(basenode)
                if base.top or base.top_sorts:raise ConfigurationError("Native region load needs a closed finite receiver set")
                keys=[expr.attr] if isinstance(expr,ast.Attribute) else known(compile_expression(expr.slice)).exact_constants()
                if keys is None:raise ConfigurationError("Native region load requires finite builtin keys")
                scalar=replace(base,refs=frozenset(),unbound=False,relations=frozenset())
                if not scalar.is_bottom():
                    if isinstance(expr,ast.Attribute):
                        scalar_values=scalar.exact_constants()
                        if scalar_values is None or any(known_builtin_attribute(type(value).__name__,expr.attr) for value in scalar_values):raise ConfigurationError("Native scalar attribute protocol is outside the direct-field profile")
                        exceptions.add("AttributeError")
                    else:
                        concrete_bases=scalar.exact_constants()
                        if concrete_bases is None:raise ConfigurationError("Native region requires a closed builtin scalar subscript")
                        values=[]
                        for concrete in concrete_bases:
                            for key in keys:
                                try:values.append(Value.constant(concrete[key]))
                                except (TypeError,IndexError,KeyError) as error:exceptions.add(type(error).__name__)
                        kernel.add_fact(target,driver.ops.join_all(values),"builtin_scalar_subscript")
                if not base.refs:return target
                for key in keys:
                    for ref in base.refs:
                        if isinstance(expr,ast.Attribute) and (boundary.heap.get(ref) or allocations.get(ref)).kind in {"dict","list","tuple","set","range"}:
                            if known_builtin_attribute((boundary.heap.get(ref) or allocations.get(ref)).kind,key):raise ConfigurationError("Native builtin attribute protocol is outside the direct-field profile")
                            exceptions.add("AttributeError");continue
                        obj=direct(ref,key)
                        actual=key
                        if isinstance(expr,ast.Subscript):
                            if obj.kind=="set":exceptions.add("TypeError");continue
                            if obj.kind in {"list","tuple"}:
                                if type(actual) not in {int,bool}:exceptions.add("TypeError");continue
                                length=obj.metadata.get("length")
                                if length is not None:
                                    if actual<0:actual+=length
                                    if actual<0 or actual>=length:exceptions.add("IndexError");continue
                        specific=temp();kernel.add_fact(specific,Value.ref(ref),"valid_load_receiver");kernel.add_load(specific,actual,target)
                        if not obj.default.is_bottom():kernel.add_edge(("default",ref),target)
                        if actual not in obj.fields or actual in obj.maybe_missing:exceptions.add("AttributeError" if isinstance(expr,ast.Attribute) else "KeyError" if obj.kind=="dict" else "IndexError")
                        loads.append((ref,actual))
                return target
            if isinstance(expr,ast.BinOp):
                left=compile_expression(expr.left);right=compile_expression(expr.right)
                def operation(a,b):
                    if a.refs or b.refs or a.top or b.top:raise ConfigurationError("Native scalar region cannot bypass user operators")
                    return driver.ops.scalar_binary(expr.op,a,b)
                kernel.add_operation((left,right),target,operation);return target
            if isinstance(expr,ast.UnaryOp):
                operand=compile_expression(expr.operand)
                if isinstance(expr.op,ast.Not):
                    def negation(value):
                        if value.refs or value.top:raise ConfigurationError("Native region cannot bypass object truth protocols")
                        return Value(bools=frozenset(not x for x in driver.ops.truth(value))),set()
                    kernel.add_operation((operand,),target,negation)
                else:
                    zero=temp();kernel.add_fact(zero,Value.constant(-1 if isinstance(expr.op,ast.Invert) else 0),"unary_constant")
                    if isinstance(expr.op,ast.Invert):kernel.add_operation((operand,zero),target,lambda a,b:driver.ops.scalar_binary(ast.BitXor(),a,b))
                    else:kernel.add_operation((zero,operand),target,lambda a,b:driver.ops.scalar_binary(ast.Sub() if isinstance(expr.op,ast.USub) else ast.Add(),a,b))
                return target
            raise ConfigurationError("Unsupported Native region expression")
        def assign(target,source):
            nonlocal normal_possible
            if isinstance(target,ast.Name):
                output=("exit",frame.cell(target.id));kernel.add_edge(source,output);cell_outputs[frame.cell(target.id)]=output
            elif isinstance(target,(ast.Attribute,ast.Subscript)):
                basenode=compile_expression(target.value);base=known(basenode)
                if base.top or base.top_sorts:raise ConfigurationError("Native region write requires a closed receiver set")
                keys=[target.attr] if isinstance(target,ast.Attribute) else known(compile_expression(target.slice)).exact_constants()
                if keys is None:raise ConfigurationError("Native region store requires finite builtin keys")
                valid=False
                for key in keys:
                    for ref in base.refs:
                        obj=direct(ref,key,True)
                        if isinstance(target,ast.Attribute) and obj.kind in {"dict","list","tuple","set","range"}:exceptions.add("AttributeError");continue
                        if isinstance(target,ast.Subscript) and obj.kind not in {"dict","list"}:exceptions.add("TypeError");continue
                        actual=key
                        if isinstance(target,ast.Subscript) and obj.kind=="list":
                            if type(actual) not in {int,bool}:exceptions.add("TypeError");continue
                            length=obj.metadata.get("length")
                            if length is not None:
                                if actual<0:actual+=length
                                if actual<0 or actual>=length:exceptions.add("IndexError");continue
                            else:exceptions.add("IndexError")
                        valid=True;field_writes.append((ref,actual,source,len(base.refs)==1 and len(keys)==1))
                        specific=temp();kernel.add_fact(specific,Value.ref(ref),"valid_store_receiver");kernel.add_store(specific,actual,source)
                if not base.refs:exceptions.add("AttributeError" if isinstance(target,ast.Attribute) else "TypeError")
                normal_possible=normal_possible and valid
            elif isinstance(target,(ast.Tuple,ast.List)):
                value=known(source)
                if len(value.refs)!=1:raise ConfigurationError("Native unpack requires one bounded sequence shape")
                objid=next(iter(value.refs));obj=direct(objid,0)
                if obj.metadata.get("length")!=len(target.elts):exceptions.add("ValueError");return
                for index,item in enumerate(target.elts):
                    component=temp();kernel.add_edge(("field",objid,index),component);assign(item,component)
            else:raise ConfigurationError("Unsupported Native assignment target")
        if isinstance(node,(ast.Assign,ast.AnnAssign)):
            if node.value is not None:
                value=compile_expression(node.value)
                for target in node.targets if isinstance(node,ast.Assign) else (node.target,):assign(target,value)
        elif isinstance(node,ast.AugAssign):
            a=compile_expression(node.target);b=compile_expression(node.value);value=temp();kernel.add_operation((a,b),value,lambda x,y:driver.ops.scalar_binary(node.op,x,y));assign(node.target,value)
        elif isinstance(node,(ast.Expr,ast.Return)):
            result_node=compile_expression(node.value) if node.value is not None else temp()
            if node.value is None:kernel.add_fact(result_node,Value.constant(None))
            if isinstance(node,ast.Return):completion="return"
        elif isinstance(node,ast.Raise):raise ConfigurationError("Native region raise requires completion-aware Python exception planning")
        settle()
        for objid,obj in allocations.items():
            for key in obj.fields:obj.fields[key]=kernel.value(("field",objid,key))
            boundary.heap[objid]=obj
        for cell,target in cell_outputs.items():
            boundary.cells[cell]=kernel.value(target).without_unbound();boundary.writes.add(("cell",cell,None))
            if driver.oracle is not None and hasattr(driver.oracle,"publish_cell"):driver.oracle.publish_cell(cell,boundary.cells[cell],"native_region_output")
        for ref,key,source,one_target in field_writes:
            obj=boundary.heap[ref];value=kernel.value(source)
            # Strong updates require the explicit boundary cardinality proof.
            if obj.singleton and one_target:obj.fields[key]=value
            else:obj.fields[key]=driver.ops.join(obj.fields.get(key,obj.default),value)
            obj.maybe_missing.discard(key);boundary.writes.add(("heap",ref,key))
            if driver.oracle is not None and hasattr(driver.oracle,"publish_field"):driver.oracle.publish_field(ref,key,obj.fields[key],"native_region_output")
        boundary.completion=completion;boundary.value=kernel.value(result_node) if result_node is not None else Value.constant(None)
        for ref,key in loads:driver.reads.add((ref,str(key),driver.point(node)))
        for cell in cell_outputs:driver.writes.add((frame.qualname,cell,driver.point(node)))
        for ref,key,_,_ in field_writes:driver.writes.add((ref,str(key),driver.point(node)))
        self.runs+=1;self.work+=kernel.work
        trace={"engine":"native","phase":"bounded_region_inclusion","region":driver.point(node),"facts":len(kernel.facts),"work":kernel.work,"input_cells":len(flow.store.cells),"input_heap_objects":len(flow.store.heap)}
        self.trace.append(trace);driver.phase_trace.append(trace);driver.stats["native_region_runs"]+=1
        output=[]
        if normal_possible and not (result_node is not None and boundary.value.is_bottom()) and all(not boundary.cells[cell].is_bottom() for cell in cell_outputs) and all(not kernel.value(source).is_bottom() for _,_,source,_ in field_writes):output.append(self.adapter.decode(boundary))
        for name in exceptions|kernel.errors:
            if name.startswith(("unknown_","resource_")):raise ConfigurationError("Native region scalar operation has an open protocol")
            exceptional=self.adapter.encode(flow);exceptional.completion="raise";exceptional.value=Value.constant(name);exceptional.exception=name;output.append(self.adapter.decode(exceptional));driver.exceptions.add(name)
        return output
