"""Async facts helper for k-CFA pointer analysis.

This module provides the AsyncFactsHelper class that collects and indexes
async-related facts from Python programs. It integrates with the KCFA2PointerAnalysis
to extract async objects and relations for downstream modeling and queries.

The helper produces machine-readable fact tables that can be exported as JSONL
or queried programmatically for async program analysis.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Iterator

from .config import KCFAConfig
from .async_types import (
    AsyncFact, CoroutineDefFact, AwaitEdgeFact, TaskCreateFact, TaskStateFact,
    FutureFact, QueueAllocFact, QueuePutFact, QueueGetFact, SyncAllocFact,
    SyncOpFact, LoopCallbackScheduleFact, CallbackEdgeFact, StreamFact,
    FactCollection, SiteId, FuncSymbol, AllocId, VarName
)

# Type imports for analysis integration
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .analysis import KCFA2PointerAnalysis


__all__ = ["AsyncFactsHelper"]


class AsyncFactsHelper:
    """Helper for collecting and indexing async-related facts.
    
    This class analyzes Python coroutine primitives and asyncio-style concurrency
    patterns in conjunction with k-CFA pointer analysis. It produces structured
    fact records that capture async objects and their relationships.
    
    The helper supports incremental analysis by processing individual functions
    or entire modules, and provides both programmatic query APIs and export
    capabilities for downstream tools.
    
    Example usage:
        >>> config = KCFAConfig(k=1, obj_depth=2)
        >>> helper = AsyncFactsHelper(config)
        >>> helper.index_module(ir_module, kcfa_analysis)
        >>> facts = helper.facts()
        >>> awaiters = helper.awaiters_of("my_module.my_coro")
        >>> helper.write_jsonl("async_facts.jsonl")
    """
    
    def __init__(self, config: Optional[KCFAConfig] = None):
        """Initialize async facts helper.
        
        Args:
            config: k-CFA configuration to use for site ID generation and context
                   matching. If None, uses default configuration.
        """
        self.config = config or KCFAConfig()
        
        # Fact storage organized by fact type
        self._facts: FactCollection = {
            "coroutine_def": [],
            "await_edge": [],
            "task_create": [],
            "task_state": [],
            "future": [],
            "queue_alloc": [],
            "queue_put": [],
            "queue_get": [],
            "sync_alloc": [],
            "sync_op": [],
            "loop_cb_schedule": [],
            "callback_edge": [],
            "stream": []
        }
        
        # Index structures for efficient queries
        self._awaiter_index: Dict[FuncSymbol, List[AwaitEdgeFact]] = {}
        self._awaited_index: Dict[FuncSymbol, List[AwaitEdgeFact]] = {}
        self._task_creator_index: Dict[FuncSymbol, List[TaskCreateFact]] = {}
        self._callback_scheduler_index: Dict[FuncSymbol, List[LoopCallbackScheduleFact]] = {}
        
        # Analysis statistics
        self._stats = {
            "functions_processed": 0,
            "async_functions_found": 0,
            "await_expressions_found": 0,
            "task_creations_found": 0,
            "queue_operations_found": 0,
            "sync_operations_found": 0,
            "callback_schedules_found": 0
        }
    
    def index_function(self, fn_ir_or_tac: Any, kcfa: Optional["KCFA2PointerAnalysis"] = None) -> None:
        """Index async facts from a single function.
        
        This method processes a function's IR or TAC representation and extracts
        async-related facts using the provided k-CFA pointer analysis results.
        
        Args:
            fn_ir_or_tac: Function IR or TAC representation
            kcfa: k-CFA pointer analysis instance with completed results (optional)
        """
        from .ir_adapter import iter_function_events
        
        # Update statistics
        self._stats["functions_processed"] += 1
        
        # Extract events from the function
        try:
            events = list(iter_function_events(fn_ir_or_tac))
        except Exception:
            # If event extraction fails, skip this function
            return
        
        # Get function name for context
        func_name = "unknown"
        if hasattr(fn_ir_or_tac, 'name'):
            func_name = fn_ir_or_tac.name
        elif hasattr(fn_ir_or_tac, 'get_name'):
            func_name = fn_ir_or_tac.get_name()
        
        # Process each event and convert to facts
        for event in events:
            try:
                self._process_event(event, func_name, kcfa)
            except Exception:
                # Skip malformed events
                continue
    
    def _process_event(self, event: Dict[str, Any], func_name: str, kcfa: Optional["KCFA2PointerAnalysis"]) -> None:
        """Process a single event and convert to appropriate facts."""
        event_kind = event.get("kind", "")
        
        if event_kind == "coroutine_def":
            self._process_coroutine_def_event(event, func_name)
        elif event_kind == "await":
            self._process_await_event(event, func_name, kcfa)
        elif event_kind == "task_create":
            self._process_task_create_event(event, func_name, kcfa)
        elif event_kind == "queue_alloc":
            self._process_queue_alloc_event(event, func_name)
        elif event_kind == "queue_op":
            self._process_queue_op_event(event, func_name, kcfa)
        elif event_kind == "sync_alloc":
            self._process_sync_alloc_event(event, func_name)
        elif event_kind == "sync_op":
            self._process_sync_op_event(event, func_name, kcfa)
        elif event_kind == "loop_cb_schedule":
            self._process_loop_callback_event(event, func_name, kcfa)
        elif event_kind == "stream":
            self._process_stream_event(event, func_name)
    
    def _process_coroutine_def_event(self, event: Dict[str, Any], func_name: str) -> None:
        """Process a coroutine definition event."""
        fact = CoroutineDefFact(
            fact_type="coroutine_def",
            func_sym=event.get("func_sym", func_name),
            def_site=event.get("def_site", "unknown"),
            is_async=event.get("is_async", True),
            is_async_gen=event.get("is_async_gen", False)
        )
        
        self._facts["coroutine_def"].append(fact)
        self._stats["async_functions_found"] += 1
        self._update_indices(fact)
    
    def _process_await_event(self, event: Dict[str, Any], func_name: str, kcfa: Optional["KCFA2PointerAnalysis"]) -> None:
        """Process an await expression event."""
        awaited_targets = []
        may_unknown = False
        
        # Try to resolve awaited targets using kcfa if available
        if kcfa and event.get("awaited_expr"):
            try:
                targets = self._resolve_awaited_targets(event["awaited_expr"], kcfa)
                if targets:
                    awaited_targets = targets
                else:
                    may_unknown = True
            except Exception:
                may_unknown = True
        else:
            # Fall back to direct symbol resolution
            awaited_expr = event.get("awaited_expr", "")
            if awaited_expr and awaited_expr != "unknown":
                awaited_targets = [awaited_expr]
            else:
                may_unknown = True
        
        fact = AwaitEdgeFact(
            fact_type="await_edge",
            await_id=event.get("await_id", "unknown"),
            awaiter_fn=func_name,
            awaited_targets=awaited_targets,
            may_unknown=may_unknown
        )
        
        self._facts["await_edge"].append(fact)
        self._stats["await_expressions_found"] += 1
        self._update_indices(fact)
    
    def _process_task_create_event(self, event: Dict[str, Any], func_name: str, kcfa: Optional["KCFA2PointerAnalysis"]) -> None:
        """Process a task creation event."""
        targets = []
        may_unknown = False
        
        # Try to resolve coroutine targets using kcfa if available
        if kcfa and event.get("coro_arg"):
            try:
                resolved_targets = self._resolve_coroutine_targets(event["coro_arg"], kcfa)
                if resolved_targets:
                    targets = resolved_targets
                else:
                    may_unknown = True
            except Exception:
                may_unknown = True
        else:
            # Fall back to direct symbol resolution
            coro_arg = event.get("coro_arg", "")
            if coro_arg and coro_arg != "unknown":
                targets = [coro_arg]
            else:
                may_unknown = True
        
        fact = TaskCreateFact(
            fact_type="task_create",
            task_id=event.get("task_id", "unknown"),
            creator_fn=func_name,
            targets=targets,
            args_vars=[event.get("coro_arg", "")] if event.get("coro_arg") else [],
            may_unknown=may_unknown
        )
        
        self._facts["task_create"].append(fact)
        self._stats["task_creations_found"] += 1
        self._update_indices(fact)
    
    def _process_queue_alloc_event(self, event: Dict[str, Any], func_name: str) -> None:
        """Process a queue allocation event."""
        fact = QueueAllocFact(
            fact_type="queue_alloc",
            queue_id=event.get("queue_id", "unknown"),
            queue_kind=event.get("queue_kind", "Queue"),
            maxsize=0,  # Default to unbounded
            alloc_ctx=func_name
        )
        
        self._facts["queue_alloc"].append(fact)
        self._stats["queue_operations_found"] += 1
    
    def _process_queue_op_event(self, event: Dict[str, Any], func_name: str, kcfa: Optional["KCFA2PointerAnalysis"]) -> None:
        """Process a queue operation event."""
        op_type = event.get("op_type", "")
        
        if op_type in ("put", "put_nowait"):
            fact = QueuePutFact(
                fact_type="queue_put",
                site_id=event.get("op_id", "unknown"),
                queue_ids=self._resolve_queue_ids(event.get("queue_var", ""), kcfa),
                value_vars=[event.get("value_var", "")] if event.get("value_var") else []
            )
            self._facts["queue_put"].append(fact)
        
        elif op_type in ("get", "get_nowait"):
            fact = QueueGetFact(
                fact_type="queue_get",
                site_id=event.get("op_id", "unknown"),
                queue_ids=self._resolve_queue_ids(event.get("queue_var", ""), kcfa),
                target_var=event.get("target_var", "")
            )
            self._facts["queue_get"].append(fact)
        
        self._stats["queue_operations_found"] += 1
    
    def _process_sync_alloc_event(self, event: Dict[str, Any], func_name: str) -> None:
        """Process a synchronization primitive allocation event."""
        fact = SyncAllocFact(
            fact_type="sync_alloc",
            sync_id=event.get("sync_id", "unknown"),
            kind=event.get("sync_kind", "Lock")
        )
        
        self._facts["sync_alloc"].append(fact)
        self._stats["sync_operations_found"] += 1
    
    def _process_sync_op_event(self, event: Dict[str, Any], func_name: str, kcfa: Optional["KCFA2PointerAnalysis"]) -> None:
        """Process a synchronization operation event."""
        fact = SyncOpFact(
            fact_type="sync_op",
            site_id=event.get("op_id", "unknown"),
            kind="unknown",  # Will be resolved from sync object
            op=event.get("op_type", ""),
            target_ids=self._resolve_sync_ids(event.get("sync_var", ""), kcfa)
        )
        
        self._facts["sync_op"].append(fact)
        self._stats["sync_operations_found"] += 1
    
    def _process_loop_callback_event(self, event: Dict[str, Any], func_name: str, kcfa: Optional["KCFA2PointerAnalysis"]) -> None:
        """Process a loop callback scheduling event."""
        callback_targets = []
        
        # Try to resolve callback targets
        if kcfa and event.get("callback_expr"):
            try:
                targets = self._resolve_callback_targets(event["callback_expr"], kcfa)
                if targets:
                    callback_targets = targets
            except Exception:
                pass
        
        # Fall back to direct symbol resolution
        if not callback_targets and event.get("callback_expr"):
            callback_expr = event["callback_expr"]
            if callback_expr and callback_expr != "unknown":
                callback_targets = [callback_expr]
        
        fact = LoopCallbackScheduleFact(
            fact_type="loop_cb_schedule",
            cb_id=event.get("cb_id", "unknown"),
            api=event.get("api", "call_soon"),
            delay=None,  # Could be extracted from call arguments
            callback_targets=callback_targets,
            args_vars=[]  # Could be extracted from call arguments
        )
        
        self._facts["loop_cb_schedule"].append(fact)
        self._stats["callback_schedules_found"] += 1
    
    def _process_stream_event(self, event: Dict[str, Any], func_name: str) -> None:
        """Process a stream operation event."""
        fact = StreamFact(
            fact_type="stream",
            site_id=event.get("stream_id", "unknown"),
            api=event.get("api", ""),
            reader_var=None,  # Could be extracted from return values
            writer_var=None   # Could be extracted from return values
        )
        
        self._facts["stream"].append(fact)
    
    # Helper methods for resolving targets using k-CFA analysis
    
    def _resolve_awaited_targets(self, awaited_expr: str, kcfa: "KCFA2PointerAnalysis") -> List[str]:
        """Resolve awaited expression targets using k-CFA analysis."""
        if not kcfa or not awaited_expr:
            return []
        
        try:
            # Get points-to set for the awaited expression
            pts = kcfa.get_points_to_for_var(awaited_expr)
            targets = []
            
            for obj in pts.objects:
                # Extract coroutine/task/future targets from object allocation IDs
                alloc_id = obj.alloc_id
                
                # Parse allocation ID to determine type and target
                if "coro" in alloc_id or "func" in alloc_id:
                    # Extract function name from allocation ID
                    parts = alloc_id.split(":")
                    if len(parts) >= 5 and parts[3] in ("func", "coro"):
                        func_name = parts[4]
                        targets.append(func_name)
                elif "task" in alloc_id:
                    # For tasks, look up the wrapped coroutine
                    from .heap_model import attr_key
                    coro_field = attr_key("_coro")
                    coro_pts = kcfa._get_field_pts(obj, coro_field)
                    
                    for coro_obj in coro_pts.objects:
                        coro_parts = coro_obj.alloc_id.split(":")
                        if len(coro_parts) >= 5 and coro_parts[3] in ("func", "coro"):
                            coro_func = coro_parts[4]
                            targets.append(coro_func)
            
            return targets
            
        except Exception:
            # Fallback for any errors
            return []
    
    def _resolve_coroutine_targets(self, coro_arg: str, kcfa: "KCFA2PointerAnalysis") -> List[str]:
        """Resolve coroutine argument targets using k-CFA analysis."""
        if not kcfa or not coro_arg:
            return []
        
        try:
            # Get points-to set for the coroutine argument
            pts = kcfa.get_points_to_for_var(coro_arg)
            targets = []
            
            for obj in pts.objects:
                # Extract coroutine function targets from object allocation IDs
                alloc_id = obj.alloc_id
                
                # Parse allocation ID to determine coroutine function
                if "coro" in alloc_id or "func" in alloc_id:
                    parts = alloc_id.split(":")
                    if len(parts) >= 5 and parts[3] in ("func", "coro"):
                        func_name = parts[4]
                        targets.append(func_name)
            
            return targets
            
        except Exception:
            # Fallback for any errors
            return []
    
    def _resolve_queue_ids(self, queue_var: str, kcfa: Optional["KCFA2PointerAnalysis"]) -> List[str]:
        """Resolve queue variable to allocation IDs using k-CFA analysis."""
        if not kcfa or not queue_var or queue_var == "unknown":
            return [queue_var] if queue_var != "unknown" else []
        
        try:
            # Get points-to set for the queue variable
            pts = kcfa.get_points_to_for_var(queue_var)
            queue_ids = []
            
            for obj in pts.objects:
                # Extract queue allocation IDs
                alloc_id = obj.alloc_id
                
                # Filter for queue objects
                if "queue" in alloc_id.lower():
                    queue_ids.append(alloc_id)
            
            return queue_ids if queue_ids else [queue_var]
            
        except Exception:
            # Fallback for any errors
            return [queue_var]
    
    def _resolve_sync_ids(self, sync_var: str, kcfa: Optional["KCFA2PointerAnalysis"]) -> List[str]:
        """Resolve sync variable to allocation IDs using k-CFA analysis."""
        if not kcfa or not sync_var or sync_var == "unknown":
            return [sync_var] if sync_var != "unknown" else []
        
        try:
            # Get points-to set for the sync variable
            pts = kcfa.get_points_to_for_var(sync_var)
            sync_ids = []
            
            for obj in pts.objects:
                # Extract sync primitive allocation IDs
                alloc_id = obj.alloc_id
                
                # Filter for sync objects (lock, semaphore, event, condition)
                if any(sync_type in alloc_id.lower() for sync_type in ["lock", "semaphore", "event", "condition"]):
                    sync_ids.append(alloc_id)
            
            return sync_ids if sync_ids else [sync_var]
            
        except Exception:
            # Fallback for any errors
            return [sync_var]
    
    def _resolve_callback_targets(self, callback_expr: str, kcfa: "KCFA2PointerAnalysis") -> List[str]:
        """Resolve callback expression targets using k-CFA analysis."""
        if not kcfa or not callback_expr:
            return []
        
        try:
            # Use the call target resolution API
            targets = kcfa.get_call_targets_for_expr(callback_expr)
            return targets
            
        except Exception:
            # Fallback for any errors
            return []
    
    def index_module(self, ir_or_tac_module: Any, kcfa: Optional["KCFA2PointerAnalysis"] = None) -> None:
        """Index async facts from an entire module.
        
        This method processes all functions in a module and collects async facts
        comprehensively. It's the recommended entry point for whole-program analysis.
        
        Args:
            ir_or_tac_module: Module IR or TAC representation
            kcfa: k-CFA pointer analysis instance with completed results (optional)
        """
        # Try to extract functions from the module
        functions = []
        
        if hasattr(ir_or_tac_module, 'functions'):
            functions = ir_or_tac_module.functions
        elif hasattr(ir_or_tac_module, 'get_functions'):
            functions = ir_or_tac_module.get_functions()
        elif hasattr(ir_or_tac_module, 'body'):
            # Look for function definitions in the module body
            from pythonstan.ir.ir_statements import IRFunc
            functions = [stmt for stmt in ir_or_tac_module.body if isinstance(stmt, IRFunc)]
        elif hasattr(ir_or_tac_module, 'stmts'):
            # Look for function definitions in the module statements
            from pythonstan.ir.ir_statements import IRFunc
            functions = [stmt for stmt in ir_or_tac_module.stmts if isinstance(stmt, IRFunc)]
        
        # Process each function
        for func in functions:
            try:
                self.index_function(func, kcfa)
            except Exception:
                # Skip functions that cause errors
                continue
    
    def facts(self) -> FactCollection:
        """Get all collected async facts.
        
        Returns:
            Dictionary mapping fact type names to lists of fact records.
            Fact types include: coroutine_def, await_edge, task_create, task_state,
            future, queue_alloc, queue_put, queue_get, sync_alloc, sync_op,
            loop_cb_schedule, callback_edge, stream.
        """
        import copy
        return copy.deepcopy(self._facts)  # Return deep copy to prevent mutation
    
    def write_jsonl(self, path: Union[str, Path]) -> None:
        """Export facts to JSONL format.
        
        Each line in the output file contains a JSON object representing one fact.
        All facts include a 'fact_type' field for filtering during import.
        
        Args:
            path: Output file path for JSONL export
            
        Example output format:
            {"fact_type": "coroutine_def", "func_sym": "main.my_coro", ...}
            {"fact_type": "await_edge", "await_id": "main.py:10:5:await", ...}
        """
        path = Path(path)
        
        with path.open('w', encoding='utf-8') as f:
            # Write all facts in order by type
            for fact_type, fact_list in self._facts.items():
                for fact in fact_list:
                    json.dump(fact, f, ensure_ascii=False, separators=(',', ':'))
                    f.write('\n')
    
    def write_csv(self, path: Union[str, Path], fact_type: str) -> None:
        """Export specific fact type to CSV format.
        
        Args:
            path: Output file path for CSV export
            fact_type: Type of facts to export (e.g., "await_edge", "task_create")
            
        Raises:
            NotImplementedError: CSV export implementation pending
        """
        raise NotImplementedError("CSV export not yet implemented")
    
    # Query API methods
    
    def awaiters_of(self, func_sym: FuncSymbol) -> List[AwaitEdgeFact]:
        """Find all await expressions that target a specific function.
        
        Args:
            func_sym: Function symbol to search for (e.g., "module.func_name")
            
        Returns:
            List of await edge facts where the function appears in awaited_targets
        """
        return self._awaited_index.get(func_sym, [])
    
    def awaited_by(self, func_sym: FuncSymbol) -> List[AwaitEdgeFact]:
        """Find all await expressions within a specific function.
        
        Args:
            func_sym: Function symbol containing await expressions
            
        Returns:
            List of await edge facts originating from the function
        """
        return self._awaiter_index.get(func_sym, [])
    
    def tasks_created_in(self, func_sym: FuncSymbol) -> List[TaskCreateFact]:
        """Find all task creation sites within a specific function.
        
        Args:
            func_sym: Function symbol containing task creations
            
        Returns:
            List of task creation facts originating from the function
        """
        return self._task_creator_index.get(func_sym, [])
    
    def queues_flowing_into(self, var_or_attr: VarName) -> List[QueueAllocFact]:
        """Find queue allocations that may flow into a variable or attribute.
        
        This method uses the k-CFA pointer analysis results to determine which
        queue objects may be pointed to by the given variable or attribute.
        
        Args:
            var_or_attr: Variable name or attribute reference
            
        Returns:
            List of queue allocation facts that may flow to the location
            
        Raises:
            NotImplementedError: Flow analysis integration pending
        """
        raise NotImplementedError("Queue flow analysis not yet implemented")
    
    def callbacks_scheduled_by(self, loop_var: VarName) -> List[LoopCallbackScheduleFact]:
        """Find callbacks scheduled by a specific event loop variable.
        
        Args:
            loop_var: Variable name referring to an event loop object
            
        Returns:
            List of callback scheduling facts for the event loop
            
        Raises:
            NotImplementedError: Callback analysis integration pending
        """
        raise NotImplementedError("Callback scheduling analysis not yet implemented")
    
    def async_generators(self) -> List[CoroutineDefFact]:
        """Find all async generator functions (async def + yield).
        
        Returns:
            List of coroutine definition facts where is_async_gen is True
        """
        coroutine_facts = self._facts["coroutine_def"]
        return [fact for fact in coroutine_facts if fact["is_async_gen"]]
    
    def sync_primitives_by_type(self, sync_type: str) -> List[SyncAllocFact]:
        """Find synchronization primitives of a specific type.
        
        Args:
            sync_type: Type of sync primitive (Lock, Semaphore, Event, Condition)
            
        Returns:
            List of sync allocation facts for the specified type
        """
        sync_facts = self._facts["sync_alloc"]
        return [fact for fact in sync_facts if fact["kind"] == sync_type]
    
    def statistics(self) -> Dict[str, int]:
        """Get analysis statistics.
        
        Returns:
            Dictionary with counts of processed functions and found async patterns
        """
        return dict(self._stats)  # Return copy to prevent mutation
    
    def clear(self) -> None:
        """Clear all collected facts and reset statistics.
        
        This method is useful for reusing the helper instance across multiple
        analysis runs or for freeing memory after exporting facts.
        """
        for fact_list in self._facts.values():
            fact_list.clear()
        
        self._awaiter_index.clear()
        self._awaited_index.clear()
        self._task_creator_index.clear()
        self._callback_scheduler_index.clear()
        
        for key in self._stats:
            self._stats[key] = 0
    
    # Internal helper methods (to be implemented)
    
    def _extract_coroutine_def_facts(self, fn_ir_or_tac: Any) -> Iterator[CoroutineDefFact]:
        """Extract coroutine definition facts from function IR/TAC."""
        raise NotImplementedError("Coroutine definition extraction not yet implemented")
    
    def _extract_await_edge_facts(self, fn_ir_or_tac: Any, kcfa: "KCFA2PointerAnalysis") -> Iterator[AwaitEdgeFact]:
        """Extract await edge facts from function IR/TAC using pointer analysis."""
        raise NotImplementedError("Await edge extraction not yet implemented")
    
    def _extract_task_create_facts(self, fn_ir_or_tac: Any, kcfa: "KCFA2PointerAnalysis") -> Iterator[TaskCreateFact]:
        """Extract task creation facts from function IR/TAC using pointer analysis."""
        raise NotImplementedError("Task creation extraction not yet implemented")
    
    def _extract_queue_facts(self, fn_ir_or_tac: Any, kcfa: "KCFA2PointerAnalysis") -> Iterator[Union[QueueAllocFact, QueuePutFact, QueueGetFact]]:
        """Extract queue-related facts from function IR/TAC using pointer analysis."""
        raise NotImplementedError("Queue facts extraction not yet implemented")
    
    def _extract_sync_facts(self, fn_ir_or_tac: Any, kcfa: "KCFA2PointerAnalysis") -> Iterator[Union[SyncAllocFact, SyncOpFact]]:
        """Extract synchronization facts from function IR/TAC using pointer analysis."""
        raise NotImplementedError("Sync facts extraction not yet implemented")
    
    def _extract_callback_facts(self, fn_ir_or_tac: Any, kcfa: "KCFA2PointerAnalysis") -> Iterator[Union[LoopCallbackScheduleFact, CallbackEdgeFact]]:
        """Extract callback scheduling facts from function IR/TAC using pointer analysis."""
        raise NotImplementedError("Callback facts extraction not yet implemented")
    
    def _extract_stream_facts(self, fn_ir_or_tac: Any, kcfa: "KCFA2PointerAnalysis") -> Iterator[StreamFact]:
        """Extract stream operation facts from function IR/TAC using pointer analysis."""
        raise NotImplementedError("Stream facts extraction not yet implemented")
    
    def _generate_site_id(self, ir_node: Any, kind: str) -> SiteId:
        """Generate site ID for async operations following the standard scheme."""
        from .ir_adapter import site_id_of
        return site_id_of(ir_node, kind)
    
    def _update_indices(self, fact: AsyncFact) -> None:
        """Update internal indices after adding a new fact."""
        fact_type = fact.get("fact_type", "")
        
        if fact_type == "await_edge":
            await_fact = fact
            # Update awaiter index
            awaiter_fn = await_fact.get("awaiter_fn", "")
            if awaiter_fn:
                if awaiter_fn not in self._awaiter_index:
                    self._awaiter_index[awaiter_fn] = []
                self._awaiter_index[awaiter_fn].append(await_fact)
            
            # Update awaited index
            for target in await_fact.get("awaited_targets", []):
                if target not in self._awaited_index:
                    self._awaited_index[target] = []
                self._awaited_index[target].append(await_fact)
        
        elif fact_type == "task_create":
            task_fact = fact
            # Update task creator index
            creator_fn = task_fact.get("creator_fn", "")
            if creator_fn:
                if creator_fn not in self._task_creator_index:
                    self._task_creator_index[creator_fn] = []
                self._task_creator_index[creator_fn].append(task_fact)
        
        elif fact_type == "loop_cb_schedule":
            cb_fact = fact
            # Update callback scheduler index (simplified)
            cb_id = cb_fact.get("cb_id", "")
            if cb_id:
                if cb_id not in self._callback_scheduler_index:
                    self._callback_scheduler_index[cb_id] = []
                self._callback_scheduler_index[cb_id].append(cb_fact)
