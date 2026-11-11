"""Generate reviewed migration/capability metadata from current source.

References are syntactic locators, not evidence of semantic effectiveness. The
explicit per-engine decisions below record the author's traced consumer review;
independent tests and reviewer records remain separate task-board obligations.
"""
from __future__ import annotations
import ast
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from pythonstan.analysis.contracts.budgets import AnalysisBudget

P="pythonstan.analysis.pointer.kcfa."
A="pythonstan.analysis.pointer.ai."
N="pythonstan.analysis.engines.abstract."
D="pythonstan.analysis.domains."

# Exact operational or rejection consumers; schema presence alone is not one.
NATIVE={
 "context_policy":("supported",P+"context_selector.ContextSelector.select_call_context","Finite registered method-context selection; all19strategies retained."),
 "max_iterations":("supported",P+"solver.PointerSolver._next_work_item","Native selected-work window bound; global resource ledger also charges nestedAI and eagerbootstrap."),
 "max_points_to_size":("rejected_nondefault",P+"config.Config.__post_init__","OnlyNone; no points-to truncation implementation."),
 "verbose":("diagnostic",P+"solver.PointerSolver._mark_unknown_call","Once-per-new-unknown-call diagnostic on the actual live path; complete semantic state/work remains invariant."),
 "log_level":("diagnostic","pythonstan.compat.native_worker.main","Requested child-local logging level; captured stderr remains outside strict NDJSON frames."),
 "enable_instrumentation":("rejected_nondefault",P+"config.Config.__post_init__","OnlyFalse; use actual solver statistics."),
 "entry_points":("legacy_only",P+"analysis.PointerAnalysis.analyze","Legacy source roots; public worker derives roots from inputs.entries and overrides this field."),
 "enable_synthetic_method_contexts":("supported_entry_expansion",P+"analysis.PointerAnalysis._create_synthetic_method_contexts","True expands actual roots with synthetic receivers; publicdefault mustbeFalse forinitialization_only, orcontract mustrecordexpansion."),
 "build_class_hierarchy":("fixed_enabled",P+"analysis.PointerAnalysis.__init__","Hierarchy alwaysconstructed; False rejected."),
 "use_mro_resolution":("fixed_enabled",P+"mro_projection.project_class_mro","MROprojection isfixedenabled; False rejected."),
 "project_path":("legacy_debug_only",P+"analysis.PointerAnalysis.analyze","Debugexportnameonly; input/import roots belongto target/Pipeline; workerinjects its owncopyroot."),
 "library_paths":("rejected_nondefault",P+"config.Config.__post_init__","Nonempty lists rejected; source/import paths belongto input/Pipeline."),
 "max_import_depth":("fixed_compatibility",P+"config.Config.__post_init__","Onlylegacydefault2; actual traversal isPipeline.import_level/inputs."),
 "track_unknowns":("diagnostic",P+"unknown_tracker.UnknownTracker.record","Controls diagnostic records; semantic unknownhavoc and effects remainactive whenFalse."),
 "log_unknown_details":("diagnostic",P+"unknown_tracker.UnknownTracker.record","Immediate diagnostic logging only."),
 "type":("fixed_tag",P+"config.Config.__post_init__","Onlypointer analysis; no strategy choice."),
 "index_sensitive":("supported",P+"processor.container.ContainerProcessor._apply_load_subscr","Key/element constraint selection and persistent native mapping consumers."),
 "work_scheduling_policy":("supported",P+"scheduler.NativeScheduler.select","legacy priority/LIFO vsfair cyclic/FIFO; no dropped work."),
 "points_work_policy":("supported",P+"worklist.ConsumerFairWorklist.pop","fifo vsconsumer_fair pointqueue; explicitpromotions/replay preserved."),
 "builtin_bootstrap_policy":("supported",P+"bootstrap.try_publish_builtin_seed","queued vseager_inert guardedpublication; eagerwork chargedgloballedger, separatefromselectedsteps."),
 "keyword_context_model":("supported",P+"processor.keyword_expansion.KeywordExpansions._partition","none vsreceiver_key_v1 finite protocolpartition; not general relational dictanalysis."),
 "keyword_context_limit":("supported",P+"processor.keyword_expansion.KeywordExpansions._partition","Exact protocolkey bucket cap withsummary/residual fallback; effectiveonlywithreceiver_key_v1."),
 "enable_debug_monitor":("unavailable_true",P+"analysis.PointerAnalysis.__init__","Optionaldebug_monitor module absent; True raisesRuntimeError; publicconfig shouldrejectbeforeanalysis."),
 "debug_log_interval":("unavailable_nondefault",P+"analysis.PointerAnalysis.__init__","Onlyoptionalmonitor consumer; solver progress logging otherwiseusesfixedinterval."),
 "track_object_flow":("unavailable_true",P+"solver.PointerSolver._apply_alloc","Monitor-gated; requiredoptionalmonitor unavailable."),
 "track_pfg_activation":("unavailable_true",P+"analysis.PointerAnalysis.__init__","Monitor-gated; requiredoptionalmonitor unavailable."),
 "export_debug_data":("unavailable_true",P+"analysis.PointerAnalysis.analyze","Monitor-gatedexport; unavailablemonitor means noactiveexport consumer."),
 "debug_output_dir":("unavailable_nondefault",P+"analysis.PointerAnalysis.__init__","Onlyoptionalmonitor outputpath; noindependentanalysisconsumer."),
 "debug_inheritance":("diagnostic",P+"solver.PointerSolver._alloc_class","Inheritance diagnostics only."),
 "enable_ai_intrinsics":("supported",P+"solver.PointerSolver._try_ai_intrinsic","Injecteddeterministic optionalgetattr/setattr/hasattr bridge; notanAI-primary mode."),
 "ai_budget":("supported_legacy_intrinsic_only","pythonstan.analysis.composition.native_intrinsics.LegacyIntrinsicProvider.apply","25validatednestedlegacyAI controls; directdispatch vsnestedCFG applicability separated; sharedgloballedger preventsfreepercallwork."),
}
LEGACY_BUDGET={
 "max_iterations":(A+"solver.AISolver.solve","CFG worklimit; directintrinsic dispatch has noCFGiterationloop."),
 "max_stmt_visits":(A+"solver.AISolver.solve","Perblock visitguard via max_block_visits."),
 "max_recursion_depth":(A+"interpreter.IRInterpreter.invoke_callee","Legacy nestedinvocation depthcap; notnewSCC scheduling."),
 "strict_exceptions":(A+"api.AISummaryEngine.analyze_callee","Internal Python exceptionreporting; shared-workexhaustion isalwaysnormalincomplete."),
 "max_addr_set_size":(A+"state.AIState.widen","Legacy full-state reabstraction/address summarization; no truncation."),
 "max_field_keys":(A+"domains.iterators.GeneratorSemantics.alloc_generator_obj","Generator/iterator metadatafield capacity; notordinarynamespacecap."),
 "max_const_strings":(A+"state.AIState.widen","Run-localStringDomaincapacity plusbounded name/key enumeration inlegacyconsumers."),
 "max_ho_tokens":(A+"domains.callable.CallableDomain.on_reduce_value","Callable/type tokenreduction; notcontext k."),
 "enable_narrowing":(A+"solver.AISolver.solve","Checked publicsummary rebuilding after narrowing."),
 "narrowing_iterations":(A+"_narrowing.NarrowingPass.run","Narrowingpasses; notfreshsolverworkbudget."),
 "enable_value_partition":(A+"domains.conditions.ConditionRefiner.refine","Legacybranch/value refinement; distinctfromwholetrace partitioning."),
 "max_value_partitions":(A+"solver.AISolver.solve","Legacypervalue/branchpartition caps viaSolverConfig andConditionSemantics."),
 "value_partition_str_cap":(A+"domains.conditions.ConditionRefiner._refine_for_str_equality","Enumeration boundforstringpredicatepartitions; notStringDomaincapacity."),
 "max_const_ints":(A+"domains.subscripts.SubscriptSemantics.load_subscr","Legacy exactindex/key enumeration cap; distinctfromnew int_domain_max_consts domaincapacity."),
 "max_tuple_len_exact":(A+"interpreter.IRInterpreter._handle_tuple_intrinsic","Exact tupleshapematerialization length; coveringfallback retained."),
 "max_tuple_alts":(A+"interpreter.IRInterpreter._handle_tuple_intrinsic","Tuple shapealternative materialization budget."),
 "max_star_expand_len":(A+"interpreter.IRInterpreter._try_expand_starred_arg","Starargument tupleexpansion length."),
 "max_star_expand_alts":(A+"interpreter.IRInterpreter._try_expand_starred_arg","Starargument shapealternatives."),
 "max_dict_parts":(A+"domains.mapslots.SlotMapSemantics._get_or_create_slots","EffectiveSlotMap partitioncap andkwargs materialization; notarbitraryobjectnamespacecap."),
 "max_dict_key_const_strs":(A+"domains.mapslots.KeyDecomposer.__init__","Finite stringkey enumerationcap."),
 "max_dict_key_const_ints":(A+"domains.mapslots.KeyDecomposer.__init__","Finite integerkey enumerationcap, usingexactenumeration contracts."),
 "max_dict_key_tuple_len":(A+"domains.mapslots.KeyDecomposer.__init__","Tuplekey decompositionlength budget."),
 "max_dict_key_tuple_alts":(A+"domains.mapslots.KeyDecomposer.__init__","Tuplekey alternatives budget."),
 "enable_dict_identity_keys":(A+"domains.mapslots.KeyDecomposer.decompose","Objectidentity keypartitions, withcoveringfallbackwhenoff."),
 "max_dict_identity_keys":(A+"domains.mapslots.KeyDecomposer.decompose","Identitykey partitioncap; earlyidentitybranch mustrespectit."),
}
# Historical locators remain provenance only after the second interpreter is
# retired. Current legacy calls either project into the one shared core or
# reject a nondefault control before executing target semantics.
HISTORICAL_LEGACY = dict(LEGACY_BUDGET)
LEGACY_SUPPORTED = {"max_iterations","max_stmt_visits","max_addr_set_size","max_const_strings",
                    "enable_narrowing","narrowing_iterations","enable_value_partition",
                    "max_value_partitions","strict_exceptions"}
LEGACY_BUDGET = {name:("pythonstan.compat.legacy_ai.api.AISummaryEngine.analyze_callee" if name=="strict_exceptions"
                else "pythonstan.compat.shared_ai.options",
                ("Host-internal error policy at direct legacy API boundary; does not prune target exceptions." if name=="strict_exceptions"
                 else "Projects into the shared source driver; exact active target consumer is listed in new_ai_consumer." if name in LEGACY_SUPPORTED
                 else "Retired private control: nondefault rejects; historical default retained only for old configuration round trips."))
          for name in HISTORICAL_LEGACY}

NEW_AI={
 "max_const_strings":(D+"values.ValueOperations.__init__","Active run-local StringDomaincapacity; used byjoin/widen."),
 "max_iterations":(N+"driver.InterproceduralDriver._solve","Perunit/SCC equation revisitlimit; globalwork isresources.max_work."),
 "max_stmt_visits":(N+"driver.InterproceduralDriver.tick","Canonical charged statement guard plus covering loop limit; zero prevents actual statement transfer."),
 "enable_narrowing":(N+"transfer.Transfer.while_loop","Controls checked looppostfixpoint refinement/publicoutcomes."),
 "narrowing_iterations":(N+"transfer.Transfer.while_loop","Maximumcheckednarrowpasses."),
 "enable_value_partition":(N+"transfer.Transfer.getitem","Actualkey-handler/value relationpartitioning, notstringcapacity."),
 "max_value_partitions":(N+"transfer.Transfer.getitem","Whole-lookup branch budget; overflow retains a covering unpartitioned key-handler union/product."),
 "max_addr_set_size":(N+"driver.InterproceduralDriver.coarsen_flows","Actual host-wide roots/heap/cells/continuations/defaults/subscriber address coarsening with weak summary writes."),
}
SOLVER_OPTIONS={
 "widen_delay":(2,"supported",N+"driver.InterproceduralDriver._request",N+"transfer.Transfer.while_loop"),
 "loop_unroll":(5,"legacy_only",A+"solver.AISolver._update_state_for_item"),
 "loop_iters":({},"legacy_only",A+"solver.AISolver._update_state_for_item"),
 "enable_trace_partitioning":(False,"legacy_only",A+"solver.AISolver.solve"),
 "infer_merge_points":(False,"legacy_only",A+"solver.AISolver.solve"),
 "merge_points":([],"legacy_only",A+"solver.AISolver.solve"),
 "max_traces_per_block":(8,"supported",N+"driver.InterproceduralDriver.compact"),
 "max_total_traces":(32,"legacy_only",A+"_trace_partition.TraceStateStore.evict_traces_if_over_budget"),
}
NEW_EXTRA={
 "abstract.context_depth":(N+"driver.InterproceduralDriver.request_function","Finite source callsite contextword length."),
 "abstract.int_domain_max_consts":(D+"values.ValueOperations.__init__","Integer widening/compression capacity; never an alias for retired legacy index-enumeration max_const_ints."),
 "abstract.relations":("pythonstan.config.resolve.resolve_config","Public alias of actual enable_value_partition; conflicting explicit spellings reject."),
 "abstract.refine_booleans":(N+"transfer.Transfer.refine_test","State cofactoring after actual truth/protocol effects; disabled mode preserves both states."),
 "abstract.max_scalar_pairs":(D+"values.ValueOperations.scalar_binary","Pure scalar Cartesian enumeration window with covering typed fallback."),
 "abstract.max_range_size":(N+"transfer.Transfer.builtin","Finite range model enumeration window; overflow is explicitly Open with covering value."),
 "abstract.attribute_name_limit":(N+"attributes.AttributeOperations.builtin","Reflective name enumeration window independent from StringDomain widening cap; residual setter covers every omitted name."),
}

class References(ast.NodeVisitor):
    def __init__(self,module,names):self.module=module;self.names=names;self.stack=[];self.result=[]
    def visit_ClassDef(self,node):
        self.stack.append(node.name);self.generic_visit(node);self.stack.pop()
    def visit_FunctionDef(self,node):
        self.stack.append(node.name);self.generic_visit(node);self.stack.pop()
    visit_AsyncFunctionDef=visit_FunctionDef
    def add(self,name,node,kind):
        self.result.append({"name":name,"symbol":".".join([self.module,*self.stack]),"line":node.lineno,"read_kind":kind})
    def visit_Attribute(self,node):
        if isinstance(node.ctx,ast.Load) and node.attr in self.names:self.add(node.attr,node,"attribute_read")
        self.generic_visit(node)
    def visit_Call(self,node):
        if isinstance(node.func,ast.Name) and node.func.id=="getattr" and len(node.args)>1 and isinstance(node.args[1],ast.Constant) and node.args[1].value in self.names:self.add(node.args[1].value,node,"getattr_read")
        if isinstance(node.func,ast.Attribute) and node.func.attr=="get" and node.args and isinstance(node.args[0],ast.Constant) and node.args[0].value in self.names:self.add(node.args[0].value,node,"mapping_read")
        self.generic_visit(node)
    def visit_Subscript(self,node):
        if isinstance(node.ctx,ast.Load) and isinstance(node.slice,ast.Constant) and node.slice.value in self.names:self.add(node.slice.value,node,"mapping_read")
        self.generic_visit(node)


def main():
    legacy=json.loads((ROOT/"pythonstan/config/legacy_defaults.json").read_text())
    budget_names=[field.name for field in fields(AnalysisBudget)]
    names=set(NATIVE)|set(budget_names)|set(SOLVER_OPTIONS)|{"context_depth","int_domain_max_consts","relations","max_scalar_pairs","max_range_size","refine_booleans","attribute_name_limit"}
    references=[];source_hashes={}
    source_roots=[ROOT/"pythonstan/analysis",ROOT/"pythonstan/compat"]
    for directory in source_roots:
        for path in sorted(directory.rglob("*.py")):
            if "/deprecated_ai/" in str(path) or "/certified/" in str(path):continue
            text=path.read_text();module=".".join(path.relative_to(ROOT).with_suffix("").parts)
            visitor=References(module,names);visitor.visit(ast.parse(text))
            if visitor.result:
                source_hashes[str(path.relative_to(ROOT))]=hashlib.sha256(text.encode()).hexdigest()
                references.extend(visitor.result)
    def refs(name,group):
        selected=[]
        for row in references:
            if row["name"]!=name:continue
            module=row["symbol"]
            if group=="native" and ".pointer.kcfa." not in module and ".native_intrinsics." not in module and ".native_worker." not in module:continue
            if group=="new" and ".engines.abstract." not in module and ".domains.values." not in module:continue
            if group=="legacy" and ".pointer.ai." not in module and ".domains." not in module and ".compat.legacy_ai." not in module and ".compat.shared_ai." not in module:continue
            if ".config." in module or ".contracts.budgets" in module:continue
            selected.append(row)
        return selected
    native=[]
    for name,default in legacy["andersen"].items():
        status,consumer,note=NATIVE[name]
        native.append({"old_name":name,"path":"andersen."+name,"old_default":default,"applies_to_engines":["native","native_then_ai"],"other_engine_consumer":("pythonstan.analysis.composition.hybrid.configure_driver" if name=="max_iterations" else None),"legacy_status":status,"actual_consumer":consumer,"meaning":note,"source_references":refs(name,"native"),"public_recommendation":"reject_nondefault_or_explicit_entry_contract" if status in {"legacy_only","legacy_debug_only","unavailable_nondefault","unavailable_true"} else "preserve_registered_behavior"})
    budgets=[]
    for name in budget_names:
        consumer,note=LEGACY_BUDGET[name]
        new=NEW_AI.get(name)
        budgets.append({"old_name":name,"old_default":getattr(AnalysisBudget(),name),"legacy_path":"andersen.ai_budget."+name,"legacy_applies_to_engines":["native","native_then_ai"],"legacy_activation":"andersen.enable_ai_intrinsics=true and eligible builtin call","legacy_consumer":consumer,"legacy_meaning":note,"legacy_status":"supported_shared_core" if name in LEGACY_SUPPORTED else "retired_reject_nondefault","historical_legacy_consumer":HISTORICAL_LEGACY[name][0],"legacy_source_references":refs(name,"legacy"),"new_path":"abstract."+name,"new_ai_status":"supported" if new else "legacy_only_reject_nondefault","new_ai_consumer":new[0] if new else None,"new_ai_meaning":new[1] if new else ("ReplacedbySCCjointfixpoint; no depthcap" if name=="max_recursion_depth" else "Noactive consumer innewAI; donotacceptas precisioncontrol"),"new_ai_source_references":refs(name,"new")})
    solver=[]
    for name,(default,status,*consumers) in SOLVER_OPTIONS.items():
        solver.append({"path":"abstract.solver_options."+name,"legacy_default":default,"new_ai_status":status,"actual_new_consumers":consumers if status=="supported" else [],"legacy_consumers":["pythonstan.compat.shared_ai.options"],"legacy_status":"supported_shared_alias" if name=="loop_unroll" else "supported_shared_core" if status=="supported" else "retired_reject_nondefault","new_references":refs(name,"new"),"legacy_references":refs(name,"legacy"),"unsupported_behavior":"rejectnondefaultinnewAI; retainlegacyintrinsic/schema compatibilityonly" if status!="supported" else "validatedactivecontrol"})
    context_tree=ast.parse((ROOT/"pythonstan/analysis/pointer/kcfa/context_selector.py").read_text())
    context_class=next(node for node in context_tree.body if isinstance(node,ast.ClassDef) and node.name=="ContextPolicy")
    context_names=[node.value.value for node in context_class.body if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant) and type(node.value.value) is str]
    contexts=[{"policy":policy,"status":"supported_finite","consumer":P+"context_selector.ContextSelector.select_call_context","heap_consumer":P+"context_selector.ContextSelector.select_alloc_context","contract":"Finiteatoms; parameterpositions/names retained; callerScope/Context notembedded; heap family projection forgetsunavailablehistory","evidence":["tests/refactor/engines/test_native_context.py","docs/refactor/evidence/20261110/PTA-01/native_matrix.json"]} for policy in context_names]
    extra=[]
    for path,(consumer,note) in NEW_EXTRA.items():
        if path in {"abstract.max_range_size","abstract.attribute_name_limit"}:
            located=refs(path.rsplit(".",1)[-1],"new")
            consumer=located[0]["symbol"] if located else None
        extra.append({"path":path,"consumer":consumer,"status":"supported" if consumer else "unavailable","meaning":note})
    data={"schema_version":1,"review_state":"author_traced_pending_independent_review","source_commit":__import__('subprocess').check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),"counts":{"native_legacy_fields":len(native),"registered_contexts":len(contexts),"legacy_ai_budget_fields":len(budgets),"additional_solver_options":len(solver)},"native_options":native,"context_policies":contexts,"ai_budget_options":budgets,"solver_options":solver,"new_options":extra,"new_ai_supported_old_budget_names":sorted(NEW_AI),"new_ai_unsupported_old_budget_names":sorted(set(budget_names)-set(NEW_AI)),"source_hashes":source_hashes,"discipline":["ASTreferences arelocators, notproofthat optionsareeffective","Perenginecapability isnotinferredfromschemaacceptance","No unsupported oldcontrol iscreditedtonewAI","Native19contextstrategies remainimplemented; notretired","Globalresources andperengineworklimits areseparate; all nestedwork charged"]}
    driver_controls={
        "pta_analysis_name":("pythonstan.compat.legacy_ai.analysis.AIAnalysisDriver._get_pta_results","Oldlocalpass predecessor selection; newCusesfrozenNativeSnapshot, notthisselector"),
        "plan_scopes":("pythonstan.compat.legacy_ai.plan.build_plan","Oldlocalpass manualscopeplan; newfunction/SCCselection belongs toregions"),
        "entry_scope":("pythonstan.compat.legacy_ai.plan.build_plan","Oldlocalpass entryscope; newroots belong toinputs.entries"),
        "context_policy":("pythonstan.compat.legacy_ai.analysis.AIAnalysisDriver._validate_context_policy","Oldpass checks inherited Nativecontext; newAIusesabstract.context_depth"),
        "log_level":("pythonstan.compat.legacy_ai.analysis.AIAnalysisDriver._setup_logging","Oldlocalpass logging only; noactive newAIlogging consumer"),
        "type":("pythonstan.compat.legacy_ai.config.Config.__post_init__","Oldfixed AIanalysis drivertag; publicengineenum controls actualexecution"),
        "solver_options":("pythonstan.compat.legacy_ai.config.Config.to_solver_config","Oldsolver optionprojection; peroption newcapabilities arelisted separately"),
    }
    data["native_heap_options"]=[
        {"path":"andersen."+name,"default":None,"applies_to_engines":["native","native_then_ai"],
         "actual_consumer":P+"context_selector.ContextSelector.select_alloc_context",
         "legacy_status":"supported","meaning":meaning}
        for name,meaning in [
            ("heap_context_policy","Heap family independently projects only represented method history; switching family may safely forget history."),
            ("heap_context_depth","Independent0..3 bound on available heap history; zero merges actual allocations and preserves may values.")]]
    data["legacy_ai_driver_options"]=[{"path":"abstract."+name,"old_default":legacy["abstract"][name],
        "legacy_consumer":consumer,"new_ai_status":"supported_subset" if name=="solver_options" else "legacy_only_reject_nondefault",
        "meaning":meaning} for name,(consumer,meaning) in driver_controls.items()]
    encoded=json.dumps(data,ensure_ascii=False,indent=2)+"\n"
    for target in (ROOT/"pythonstan/config/control_metadata.json",ROOT/"docs/refactor/old_to_new_options.json"):
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(encoded)
    print(json.dumps(data["counts"]))

if __name__=="__main__":main()
