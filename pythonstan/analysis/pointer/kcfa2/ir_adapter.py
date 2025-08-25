"""IR adapter for k-CFA pointer analysis.

This module defines the event-based interface for extracting pointer-relevant
operations from PythonStAn IR and Three-Address Code (TAC).

The adapter converts IR/TAC operations into a stream of events that the
pointer analysis can consume, abstracting over the specific IR representation.

Event schemas follow the patterns identified in docs/digests/ir-semantics-digest.md.
"""

from typing import Iterable, Optional, Dict, Any, List, Union, Protocol
from typing_extensions import TypedDict

__all__ = [
    "AllocEvent",
    "AttrLoadEvent", 
    "AttrStoreEvent",
    "ElemLoadEvent",
    "ElemStoreEvent", 
    "CallEvent",
    "ReturnEvent",
    "ExceptionEvent",
    "CoroutineDefEvent",
    "AwaitEvent",
    "TaskCreateEvent",
    "QueueAllocEvent",
    "QueueOpEvent",
    "SyncAllocEvent",
    "SyncOpEvent",
    "LoopCallbackScheduleEvent",
    "StreamEvent",
    "iter_function_events",
    "site_id_of"
]


# Event type definitions using TypedDict for structured event data

class AllocEvent(TypedDict):
    """Object allocation event.
    
    Generated from: IRAssign with object/container constructors, IRFunc, IRClass
    
    Fields:
        kind: "alloc"
        alloc_id: Unique allocation site identifier  
        target: Target variable receiving the new object
        type: Type of allocation (obj, list, tuple, dict, set, func, class, etc.)
        recv_binding: Receiver binding for method allocations (optional)
        bb: Basic block identifier
        idx: Index within basic block
    """
    kind: str  # "alloc"
    alloc_id: str
    target: str
    type: str  # obj, list, tuple, dict, set, func, class, exc, method, genframe
    recv_binding: Optional[str]
    bb: str
    idx: int


class CallEvent(TypedDict):
    """Function call event.
    
    Generated from: IRCall (direct, indirect, method)
    
    Fields:
        kind: "call"
        call_id: Unique call site identifier
        callee_expr: Callee expression string (optional)
        callee_symbol: Callee symbol name (optional)
        args: Argument variable names
        kwargs: Keyword argument mappings
        receiver: Receiver object for method calls (optional)
        target: Target variable for return value (optional)
        bb: Basic block identifier
        idx: Index within basic block
    """
    kind: str  # "call"
    call_id: str
    callee_expr: Optional[str]
    callee_symbol: Optional[str]
    args: List[str]
    kwargs: Dict[str, str]
    receiver: Optional[str]
    target: Optional[str]
    bb: str
    idx: int


class AttrLoadEvent(TypedDict):
    """Attribute load event.
    
    Generated from: IRLoadAttr
    
    Fields:
        kind: "attr_load"
        target: Target variable receiving the attribute value
        obj: Base object being accessed
        attr: Name of the attribute being loaded (optional for dynamic access)
    """
    kind: str  # "attr_load"
    target: str
    obj: str
    attr: Optional[str]


class AttrStoreEvent(TypedDict):
    """Attribute store event.
    
    Generated from: IRStoreAttr
    
    Fields:
        kind: "attr_store"
        obj: Base object being modified
        attr: Name of the attribute being stored (optional for dynamic access)
        value: Value being stored
    """
    kind: str  # "attr_store"
    obj: str
    attr: Optional[str]
    value: str


class ElemLoadEvent(TypedDict):
    """Container element load event.
    
    Generated from: IRLoadSubscr
    
    Fields:
        kind: "elem_load"
        target: Target variable receiving the element value
        container: Container being accessed
        container_kind: Type of container (list, tuple, set, dict)
        index_unknown: Whether index cannot be determined statically
    """
    kind: str  # "elem_load"
    target: str
    container: str
    container_kind: str  # "list", "tuple", "set", "dict"
    index_unknown: bool


class ElemStoreEvent(TypedDict):
    """Container element store event.
    
    Generated from: IRStoreSubscr
    
    Fields:
        kind: "elem_store"
        container: Container being modified
        container_kind: Type of container (list, tuple, set, dict)
        index_unknown: Whether index cannot be determined statically
        value: Value being stored
    """
    kind: str  # "elem_store"
    container: str
    container_kind: str  # "list", "tuple", "set", "dict"
    index_unknown: bool
    value: str


class ReturnEvent(TypedDict):
    """Function return event.
    
    Generated from: IRReturn
    
    Fields:
        kind: "return"
        value: Value being returned (None for void returns)
    """
    kind: str  # "return"
    value: Optional[str]


class ExceptionEvent(TypedDict):
    """Exception-related event.
    
    Generated from: IRRaise, IRCatchException
    
    Fields:
        kind: "exception"
        exc_alloc_id: Allocation ID of the exception object
        bb: Basic block identifier
        idx: Index within basic block
    """
    kind: str  # "exception"
    exc_alloc_id: str
    bb: str
    idx: int


class CoroutineDefEvent(TypedDict):
    """Coroutine function definition event.
    
    Generated from: IRFunc with is_async=True
    
    Fields:
        kind: "coroutine_def"
        func_sym: Function symbol name
        def_site: Definition site ID
        is_async: Always True for coroutines
        is_async_gen: True if function contains yield (async generator)
        bb: Basic block identifier
        idx: Index within basic block
    """
    kind: str  # "coroutine_def"
    func_sym: str
    def_site: str
    is_async: bool
    is_async_gen: bool
    bb: str
    idx: int


class AwaitEvent(TypedDict):
    """Await expression event.
    
    Generated from: IRAwait
    
    Fields:
        kind: "await"
        await_id: Await site ID
        awaiter_fn: Function containing the await expression
        awaited_expr: Expression being awaited
        target_var: Variable receiving the await result (optional)
        bb: Basic block identifier
        idx: Index within basic block
    """
    kind: str  # "await"
    await_id: str
    awaiter_fn: str
    awaited_expr: str
    target_var: Optional[str]
    bb: str
    idx: int


class TaskCreateEvent(TypedDict):
    """Task creation event.
    
    Generated from: asyncio.create_task() calls
    
    Fields:
        kind: "task_create"
        task_id: Task creation site ID
        creator_fn: Function containing the task creation
        coro_arg: Coroutine argument expression
        target_var: Variable receiving the task object (optional)
        bb: Basic block identifier
        idx: Index within basic block
    """
    kind: str  # "task_create"
    task_id: str
    creator_fn: str
    coro_arg: str
    target_var: Optional[str]
    bb: str
    idx: int


class QueueAllocEvent(TypedDict):
    """Queue allocation event.
    
    Generated from: asyncio.Queue/LifoQueue/PriorityQueue constructor calls
    
    Fields:
        kind: "queue_alloc"
        queue_id: Queue allocation site ID
        queue_kind: Type of queue (Queue, LifoQueue, PriorityQueue)
        target_var: Variable receiving the queue object
        bb: Basic block identifier
        idx: Index within basic block
    """
    kind: str  # "queue_alloc"
    queue_id: str
    queue_kind: str
    target_var: str
    bb: str
    idx: int


class QueueOpEvent(TypedDict):
    """Queue operation event.
    
    Generated from: queue.put/get/put_nowait/get_nowait/task_done/join calls
    
    Fields:
        kind: "queue_op"
        op_id: Operation site ID
        op_type: Operation type (put, get, put_nowait, get_nowait, task_done, join)
        queue_var: Variable referring to the queue
        value_var: Variable for put operations (optional)
        target_var: Variable for get operations (optional)
        bb: Basic block identifier
        idx: Index within basic block
    """
    kind: str  # "queue_op"
    op_id: str
    op_type: str
    queue_var: str
    value_var: Optional[str]
    target_var: Optional[str]
    bb: str
    idx: int


class SyncAllocEvent(TypedDict):
    """Synchronization primitive allocation event.
    
    Generated from: asyncio.Lock/Semaphore/Event/Condition constructor calls
    
    Fields:
        kind: "sync_alloc"
        sync_id: Sync object allocation site ID
        sync_kind: Type of sync primitive (Lock, Semaphore, BoundedSemaphore, Event, Condition)
        target_var: Variable receiving the sync object
        bb: Basic block identifier
        idx: Index within basic block
    """
    kind: str  # "sync_alloc"
    sync_id: str
    sync_kind: str
    target_var: str
    bb: str
    idx: int


class SyncOpEvent(TypedDict):
    """Synchronization operation event.
    
    Generated from: acquire/release/set/clear/wait/notify operations on sync primitives
    
    Fields:
        kind: "sync_op"
        op_id: Operation site ID
        op_type: Operation type (acquire, release, set, clear, wait, notify, notify_all)
        sync_var: Variable referring to the sync object
        target_var: Variable receiving the result (optional)
        bb: Basic block identifier
        idx: Index within basic block
    """
    kind: str  # "sync_op"
    op_id: str
    op_type: str
    sync_var: str
    target_var: Optional[str]
    bb: str
    idx: int


class LoopCallbackScheduleEvent(TypedDict):
    """Event loop callback scheduling event.
    
    Generated from: loop.call_soon/call_later calls
    
    Fields:
        kind: "loop_cb_schedule"
        cb_id: Callback scheduling site ID
        api: Loop API used (call_soon, call_later)
        callback_expr: Callback function expression
        loop_var: Variable referring to the event loop
        bb: Basic block identifier
        idx: Index within basic block
    """
    kind: str  # "loop_cb_schedule"
    cb_id: str
    api: str
    callback_expr: str
    loop_var: str
    bb: str
    idx: int


class StreamEvent(TypedDict):
    """Async stream operation event.
    
    Generated from: asyncio.open_connection/start_server calls
    
    Fields:
        kind: "stream"
        stream_id: Stream operation site ID
        api: Stream API used (open_connection, start_server)
        target_var: Variable receiving the stream objects (optional)
        bb: Basic block identifier
        idx: Index within basic block
    """
    kind: str  # "stream"
    stream_id: str
    api: str
    target_var: Optional[str]
    bb: str
    idx: int


# Union type for all events
Event = Union[
    AllocEvent, CallEvent, AttrLoadEvent, AttrStoreEvent, 
    ElemLoadEvent, ElemStoreEvent, ReturnEvent, ExceptionEvent,
    CoroutineDefEvent, AwaitEvent, TaskCreateEvent, QueueAllocEvent,
    QueueOpEvent, SyncAllocEvent, SyncOpEvent, LoopCallbackScheduleEvent,
    StreamEvent
]


# IR adapter protocols and interfaces

class IRNode(Protocol):
    """Protocol for IR/TAC nodes."""
    pass


class IRFunction(Protocol):
    """Protocol for IR/TAC functions."""
    
    def get_blocks(self) -> Iterable[Any]:
        """Get basic blocks in this function."""
        ...
        
    def get_name(self) -> str:
        """Get function name."""
        ...


def iter_function_events(fn_ir_or_tac: Union[IRFunction, Any]) -> Iterable[Event]:
    """Extract pointer-relevant events from a function's IR or TAC.
    
    This function walks through the IR/TAC representation of a function
    and yields events corresponding to operations that affect the points-to
    analysis state.
    
    Args:
        fn_ir_or_tac: Function IR or TAC representation
        
    Yields:
        Events representing pointer-relevant operations
        
    Notes:
        Key mappings:
        - IRAssign (Object) -> AllocEvent  
        - IRLoadAttr -> AttrLoadEvent
        - IRStoreAttr -> AttrStoreEvent
        - IRLoadSubscr -> ElemLoadEvent
        - IRStoreSubscr -> ElemStoreEvent
        - IRCall -> CallEvent
        - IRReturn -> ReturnEvent
        - IRRaise/IRCatchException -> ExceptionEvent
    """
    # Import PythonStAn IR classes
    try:
        from pythonstan.ir.ir_statements import (
            IRAssign, IRCall, IRLoadAttr, IRStoreAttr, 
            IRLoadSubscr, IRStoreSubscr, IRReturn, IRRaise,
            IRCatchException, IRFunc, IRClass, IRCopy
        )
    except ImportError:
        # If IR modules not available, fall back to empty iteration
        return iter([])
    
    events = []
    
    # Handle different function representations
    if hasattr(fn_ir_or_tac, 'get_blocks'):
        # Function with block structure
        try:
            blocks = fn_ir_or_tac.get_blocks()
            for block_idx, block in enumerate(blocks):
                block_id = f"bb{block_idx}"
                if hasattr(block, 'instructions'):
                    for instr_idx, instr in enumerate(block.instructions):
                        events.extend(_process_ir_instruction(instr, block_id, instr_idx))
                elif hasattr(block, '__iter__'):
                    # Block is iterable of instructions
                    for instr_idx, instr in enumerate(block):
                        events.extend(_process_ir_instruction(instr, block_id, instr_idx))
        except AttributeError:
            pass
    elif hasattr(fn_ir_or_tac, '__iter__'):
        # Function is iterable of instructions
        try:
            for instr_idx, instr in enumerate(fn_ir_or_tac):
                events.extend(_process_ir_instruction(instr, "bb0", instr_idx))
        except (AttributeError, TypeError):
            pass
    elif hasattr(fn_ir_or_tac, 'body') or hasattr(fn_ir_or_tac, 'stmts'):
        # Function has body or stmts attribute
        try:
            instructions = getattr(fn_ir_or_tac, 'body', None) or getattr(fn_ir_or_tac, 'stmts', [])
            for instr_idx, instr in enumerate(instructions):
                events.extend(_process_ir_instruction(instr, "bb0", instr_idx))
        except (AttributeError, TypeError):
            pass
    
    return iter(events)


def _process_ir_instruction(instr: Any, block_id: str, instr_idx: int) -> List[Event]:
    """Process a single IR instruction and generate relevant events."""
    events = []
    
    try:
        from pythonstan.ir.ir_statements import (
            IRAssign, IRCall, IRLoadAttr, IRStoreAttr, 
            IRLoadSubscr, IRStoreSubscr, IRReturn, IRRaise,
            IRCatchException, IRFunc, IRClass, IRCopy, IRAwait, IRYield
        )
        import ast
    except ImportError:
        return events
    
    try:
        if isinstance(instr, IRAssign):
            # IRAssign: lval = rval (check if rval is an allocation)
            rval = instr.get_rval()
            lval = instr.get_lval()
            
            alloc_type = None
            target = lval.id if hasattr(lval, 'id') else str(lval)
            
            # Check if rval is an allocation
            if isinstance(rval, ast.Call):
                if hasattr(rval.func, 'id'):
                    func_name = rval.func.id
                    if func_name in ('list', 'dict', 'tuple', 'set'):
                        alloc_type = func_name
                    elif func_name == 'object':
                        alloc_type = 'obj'
                    else:
                        # Regular function call - could be class instantiation
                        alloc_type = 'obj'
            elif isinstance(rval, ast.List):
                alloc_type = 'list'
            elif isinstance(rval, ast.Dict):
                alloc_type = 'dict'
            elif isinstance(rval, ast.Tuple):
                alloc_type = 'tuple'
            elif isinstance(rval, ast.Set):
                alloc_type = 'set'
            elif isinstance(rval, ast.ListComp):
                alloc_type = 'list'
            elif isinstance(rval, ast.DictComp):
                alloc_type = 'dict'
            elif isinstance(rval, ast.SetComp):
                alloc_type = 'set'
            elif isinstance(rval, ast.Constant):
                # Constants need to be allocated as objects
                alloc_type = 'const'
            
            if alloc_type:
                # Generate allocation event
                site_id = site_id_of(instr, 'alloc')
                events.append(AllocEvent(
                    kind="alloc",
                    alloc_id=site_id,
                    target=target,
                    type=alloc_type,
                    recv_binding=None,
                    bb=block_id,
                    idx=instr_idx
                ))
            else:
                # Regular assignment - generate copy constraint
                source = rval.id if hasattr(rval, 'id') else None
                if source:
                    events.append({
                        "kind": "copy",
                        "source": source,
                        "target": target,
                        "bb": block_id,
                        "idx": instr_idx
                    })
        
        elif isinstance(instr, IRCopy):
            # IRCopy: target = source (simple copy)
            source = instr.get_rval().id if hasattr(instr.get_rval(), 'id') else str(instr.get_rval())
            target = instr.get_lval().id if hasattr(instr.get_lval(), 'id') else str(instr.get_lval())
            
            events.append({
                "kind": "copy",
                "source": source,
                "target": target,
                "bb": block_id,
                "idx": instr_idx
            })
        
        elif isinstance(instr, IRCall):
            # IRCall: [target =] func_name(args)
            site_id = site_id_of(instr, 'call')
            callee_symbol = instr.get_func_name()
            target = instr.get_target()
            args = [arg[0] for arg in instr.get_args()]  # Extract arg names, ignore starred flag
            kwargs = {k: v for k, v in instr.get_keywords() if k is not None}
            
            # Generate call event
            events.append(CallEvent(
                kind="call",
                call_id=site_id,
                callee_expr=None,
                callee_symbol=callee_symbol,
                args=args,
                kwargs=kwargs,
                receiver=None,
                target=target,
                bb=block_id,
                idx=instr_idx
            ))
            
            # Check for async API calls and generate corresponding events
            async_events = _extract_async_events_from_call(instr, site_id, target, callee_symbol, args, block_id, instr_idx)
            events.extend(async_events)
            
            # If this call has a target, it could be an object allocation
            # Generate allocation event for potential constructor calls
            if target and callee_symbol:
                # Check if this is likely a constructor call
                # (any function call that assigns to a variable could create an object)
                if not callee_symbol in ('print', 'len', 'str', 'int', 'float', 'bool'):
                    # Generate allocation event with the same site_id but 'alloc' kind
                    alloc_site_id = site_id_of(instr, 'alloc')
                    
                    # Determine allocation type
                    if callee_symbol in ('list', 'dict', 'tuple', 'set'):
                        alloc_type = callee_symbol
                    elif callee_symbol == 'object':
                        alloc_type = 'obj'
                    else:
                        # Assume this is a class constructor
                        alloc_type = 'obj'
                    
                    events.append(AllocEvent(
                        kind="alloc",
                        alloc_id=alloc_site_id,
                        target=target,
                        type=alloc_type,
                        recv_binding=None,
                        bb=block_id,
                        idx=instr_idx
                    ))
        
        elif isinstance(instr, IRLoadAttr):
            # IRLoadAttr: target = obj.attr
            obj = instr.get_obj().id if hasattr(instr.get_obj(), 'id') else str(instr.get_obj())
            attr = instr.get_attr()
            target = instr.get_lval().id if hasattr(instr.get_lval(), 'id') else str(instr.get_lval())
            
            events.append(AttrLoadEvent(
                kind="attr_load",
                target=target,
                obj=obj,
                attr=attr
            ))
        
        elif isinstance(instr, IRStoreAttr):
            # IRStoreAttr: obj.attr = value
            obj = instr.get_obj().id if hasattr(instr.get_obj(), 'id') else str(instr.get_obj())
            attr = instr.get_attr()
            value = instr.get_rval().id if hasattr(instr.get_rval(), 'id') else str(instr.get_rval())
            
            events.append(AttrStoreEvent(
                kind="attr_store",
                obj=obj,
                attr=attr,
                value=value
            ))
        
        elif isinstance(instr, IRLoadSubscr):
            # IRLoadSubscr: target = obj[slice]
            container = instr.get_obj().id if hasattr(instr.get_obj(), 'id') else str(instr.get_obj())
            target = instr.get_lval().id if hasattr(instr.get_lval(), 'id') else str(instr.get_lval())
            
            events.append(ElemLoadEvent(
                kind="elem_load",
                target=target,
                container=container,
                container_kind="unknown",  # Could be enhanced with type info
                index_unknown=True
            ))
        
        elif isinstance(instr, IRStoreSubscr):
            # IRStoreSubscr: obj[slice] = value
            container = instr.get_obj().id if hasattr(instr.get_obj(), 'id') else str(instr.get_obj())
            value = instr.get_rval().id if hasattr(instr.get_rval(), 'id') else str(instr.get_rval())
            
            events.append(ElemStoreEvent(
                kind="elem_store",
                container=container,
                container_kind="unknown",  # Could be enhanced with type info
                index_unknown=True,
                value=value
            ))
        
        elif isinstance(instr, IRAwait):
            # IRAwait: [target =] await expr
            site_id = site_id_of(instr, 'await')
            target = None
            if instr.get_target():
                target = instr.get_target().id
            
            awaited_expr = "unknown"
            if hasattr(instr.get_value(), 'id'):
                awaited_expr = instr.get_value().id
            elif hasattr(instr.get_value(), 'attr'):
                # Handle attribute access like obj.method()
                awaited_expr = ast.unparse(instr.get_value())
            
            events.append(AwaitEvent(
                kind="await",
                await_id=site_id,
                awaiter_fn="unknown",  # Will be set by higher-level processing
                awaited_expr=awaited_expr,
                target_var=target,
                bb=block_id,
                idx=instr_idx
            ))
        
        elif isinstance(instr, IRReturn):
            # IRReturn: return value
            value = None
            if hasattr(instr, 'value') and instr.value:
                if hasattr(instr.value, 'id'):
                    value = instr.value.id
                else:
                    value = str(instr.value)
            
            events.append(ReturnEvent(
                kind="return",
                value=value
            ))
        
        elif isinstance(instr, (IRFunc, IRClass)):
            # Generate allocation event for function/class definition
            site_id = site_id_of(instr, 'func' if isinstance(instr, IRFunc) else 'class')
            name = instr.name if hasattr(instr, 'name') else 'unknown'
            
            events.append(AllocEvent(
                kind="alloc",
                alloc_id=site_id,
                target=name,
                type='func' if isinstance(instr, IRFunc) else 'class',
                recv_binding=None,
                bb=block_id,
                idx=instr_idx
            ))
            
            # If this is an async function, also generate coroutine definition event
            if isinstance(instr, IRFunc) and instr.is_async:
                # Check if it's an async generator by looking for yield
                is_async_gen = _has_yield_in_function(instr)
                
                events.append(CoroutineDefEvent(
                    kind="coroutine_def",
                    func_sym=name,
                    def_site=site_id,
                    is_async=True,
                    is_async_gen=is_async_gen,
                    bb=block_id,
                    idx=instr_idx
                ))
            
    except (AttributeError, TypeError) as e:
        # For unsupported or malformed nodes, skip silently
        # Could log warning in verbose mode
        pass
    
    return events


def _has_yield_in_function(ir_func: Any) -> bool:
    """Check if an IRFunc contains yield statements (making it an async generator)."""
    try:
        from pythonstan.ir.ir_statements import IRYield
        
        # Check the function body for yield statements
        if hasattr(ir_func, 'body'):
            for stmt in ir_func.body:
                if isinstance(stmt, IRYield):
                    return True
        elif hasattr(ir_func, 'stmts'):
            for stmt in ir_func.stmts:
                if isinstance(stmt, IRYield):
                    return True
    except (ImportError, AttributeError):
        pass
    
    return False


def _extract_async_events_from_call(ir_call: Any, call_site_id: str, target: Optional[str], 
                                   callee_symbol: Optional[str], args: List[str],
                                   block_id: str, instr_idx: int) -> List[Event]:
    """Extract async-specific events from function calls."""
    events = []
    
    if not callee_symbol:
        return events
    
    # Check for asyncio.create_task calls
    if callee_symbol == 'create_task' or 'create_task' in callee_symbol:
        task_site_id = site_id_of(ir_call, 'create_task')
        coro_arg = args[0] if args else "unknown"
        
        events.append(TaskCreateEvent(
            kind="task_create",
            task_id=task_site_id,
            creator_fn="unknown",  # Will be set by higher-level processing
            coro_arg=coro_arg,
            target_var=target,
            bb=block_id,
            idx=instr_idx
        ))
    
    # Check for asyncio.Queue constructor calls
    elif callee_symbol in ('Queue', 'LifoQueue', 'PriorityQueue') or any(queue_type in callee_symbol for queue_type in ['Queue', 'LifoQueue', 'PriorityQueue']):
        queue_site_id = site_id_of(ir_call, 'queue')
        
        # Determine queue kind
        if 'LifoQueue' in callee_symbol:
            queue_kind = 'LifoQueue'
        elif 'PriorityQueue' in callee_symbol:
            queue_kind = 'PriorityQueue'
        else:
            queue_kind = 'Queue'
        
        events.append(QueueAllocEvent(
            kind="queue_alloc",
            queue_id=queue_site_id,
            queue_kind=queue_kind,
            target_var=target or "unknown",
            bb=block_id,
            idx=instr_idx
        ))
    
    # Check for queue operations
    elif callee_symbol in ('put', 'get', 'put_nowait', 'get_nowait', 'task_done', 'join'):
        op_site_id = site_id_of(ir_call, 'queue_op')
        
        # For method calls, we need the receiver object
        queue_var = "unknown"
        if hasattr(ir_call, 'get_obj'):
            obj = ir_call.get_obj()
            if hasattr(obj, 'id'):
                queue_var = obj.id
        
        value_var = None
        target_var = None
        
        if callee_symbol in ('put', 'put_nowait'):
            value_var = args[0] if args else None
        elif callee_symbol in ('get', 'get_nowait'):
            target_var = target
        
        events.append(QueueOpEvent(
            kind="queue_op",
            op_id=op_site_id,
            op_type=callee_symbol,
            queue_var=queue_var,
            value_var=value_var,
            target_var=target_var,
            bb=block_id,
            idx=instr_idx
        ))
    
    # Check for sync primitive constructor calls
    elif callee_symbol in ('Lock', 'Semaphore', 'BoundedSemaphore', 'Event', 'Condition') or any(sync_type in callee_symbol for sync_type in ['Lock', 'Semaphore', 'Event', 'Condition']):
        sync_site_id = site_id_of(ir_call, 'sync')
        
        # Determine sync kind
        if 'BoundedSemaphore' in callee_symbol:
            sync_kind = 'BoundedSemaphore'
        elif 'Semaphore' in callee_symbol:
            sync_kind = 'Semaphore'
        elif 'Event' in callee_symbol:
            sync_kind = 'Event'
        elif 'Condition' in callee_symbol:
            sync_kind = 'Condition'
        else:
            sync_kind = 'Lock'
        
        events.append(SyncAllocEvent(
            kind="sync_alloc",
            sync_id=sync_site_id,
            sync_kind=sync_kind,
            target_var=target or "unknown",
            bb=block_id,
            idx=instr_idx
        ))
    
    # Check for sync operations
    elif callee_symbol in ('acquire', 'release', 'set', 'clear', 'wait', 'notify', 'notify_all'):
        op_site_id = site_id_of(ir_call, 'sync_op')
        
        # For method calls, we need the receiver object
        sync_var = "unknown"
        if hasattr(ir_call, 'get_obj'):
            obj = ir_call.get_obj()
            if hasattr(obj, 'id'):
                sync_var = obj.id
        
        events.append(SyncOpEvent(
            kind="sync_op",
            op_id=op_site_id,
            op_type=callee_symbol,
            sync_var=sync_var,
            target_var=target,
            bb=block_id,
            idx=instr_idx
        ))
    
    # Check for event loop callback scheduling
    elif callee_symbol in ('call_soon', 'call_later'):
        cb_site_id = site_id_of(ir_call, 'loop_cb')
        
        # For method calls, we need the receiver object (event loop)
        loop_var = "unknown"
        if hasattr(ir_call, 'get_obj'):
            obj = ir_call.get_obj()
            if hasattr(obj, 'id'):
                loop_var = obj.id
        
        callback_expr = args[0] if args else "unknown"
        
        events.append(LoopCallbackScheduleEvent(
            kind="loop_cb_schedule",
            cb_id=cb_site_id,
            api=callee_symbol,
            callback_expr=callback_expr,
            loop_var=loop_var,
            bb=block_id,
            idx=instr_idx
        ))
    
    # Check for stream operations
    elif callee_symbol in ('open_connection', 'start_server'):
        stream_site_id = site_id_of(ir_call, 'stream')
        
        events.append(StreamEvent(
            kind="stream",
            stream_id=stream_site_id,
            api=callee_symbol,
            target_var=target,
            bb=block_id,
            idx=instr_idx
        ))
    
    return events


def site_id_of(node: IRNode, kind: Optional[str] = None) -> str:
    """Extract allocation/call site ID from an IR node.
    
    Args:
        node: IR or TAC node
        kind: Optional kind override (call, alloc, obj, etc.)
        
    Returns:
        Site ID string in format file:line:col:kind or fallback format
        
    Notes:
        Site ID format follows the scheme in ir-semantics-digest.md:
        - Preferred: f"{file}:{lineno}:{col}:{kind}" 
        - Fallback: f"{file_stem}:{op}:{hash(uid)%2**32:x}"
        
        The site ID must be stable across analysis runs for reproducibility.
    """
    # Try to extract source location information
    try:
        filename = None
        lineno = None
        col = None
        
        # Check for source_location attribute first (for mock objects)
        if hasattr(node, 'source_location') and node.source_location:
            loc = node.source_location
            filename = getattr(loc, 'filename', None)
            lineno = getattr(loc, 'lineno', None)
            col = getattr(loc, 'col_offset', None)
        # Check for direct attributes (for real IR nodes)
        elif hasattr(node, 'lineno') and hasattr(node, 'col_offset') and node.lineno is not None:
            filename = getattr(node, 'filename', None)
            lineno = node.lineno
            col = node.col_offset
        # Try to get source location from AST node (for IR nodes)
        else:
            # Try get_ast() method first
            if hasattr(node, 'get_ast') and callable(node.get_ast):
                try:
                    ast_node = node.get_ast()
                    if hasattr(ast_node, 'lineno') and hasattr(ast_node, 'col_offset'):
                        lineno = ast_node.lineno
                        col = ast_node.col_offset
                        # Try to get filename from AST node or module context
                        filename = getattr(ast_node, 'filename', None)
                except (AttributeError, TypeError):
                    pass
            
            # If get_ast() failed, try ast_repr attribute (for IRClass, IRFunc)
            if lineno is None and hasattr(node, 'ast_repr'):
                try:
                    ast_node = node.ast_repr
                    if hasattr(ast_node, 'lineno') and hasattr(ast_node, 'col_offset'):
                        lineno = ast_node.lineno
                        col = ast_node.col_offset
                        # Try to get filename from AST node or module context
                        filename = getattr(ast_node, 'filename', None)
                except (AttributeError, TypeError):
                    pass
        
        # If we didn't get source location info, raise exception to fall back
        if lineno is None or col is None:
            raise AttributeError("No source location information")
            
        # Use a more meaningful filename if available, otherwise use basename
        if filename is None:
            # For temporary files or unknown sources, use a more descriptive name
            filename = 'test.py'
        else:
            import os
            basename = os.path.basename(filename)
            # Check if it's a temporary file (typically in /tmp/ with random names)
            if 'tmp' in filename and len(basename) > 10 and basename.startswith('tmp'):
                # Use a more readable name for temporary files
                filename = 'test.py'
            else:
                filename = basename
            
        # Determine kind
        if kind:
            site_kind = kind
        else:
            # Determine kind based on node type
            node_type = type(node).__name__
            if 'Assign' in node_type:
                site_kind = 'alloc'
            elif 'Call' in node_type:
                site_kind = 'call'
            elif 'Await' in node_type:
                site_kind = 'await'
            elif 'Yield' in node_type:
                site_kind = 'yield'
            else:
                site_kind = 'op'
                
        return f"{filename}:{lineno}:{col}:{site_kind}"
            
    except (AttributeError, TypeError):
        pass
    
    # Fallback: generate stable hash-based ID
    import hashlib
    
    # Try to get UID from the node if available
    if hasattr(node, 'uid'):
        uid_part = node.uid
    else:
        # Generate hash-based ID from node representation
        node_repr = repr(node)
        uid_hash = int(hashlib.md5(node_repr.encode()).hexdigest()[:8], 16)
        uid_part = f"{uid_hash:x}"
    
    # Use generic filename if no context available
    file_stem = "unknown"
    op = kind or type(node).__name__.lower()
    
    return f"{file_stem}:{op}:{uid_part}"


# Helper functions for event construction

def make_alloc_event(
    alloc_id: str,
    target: str, 
    type_: str,
    bb: str = "bb0",
    idx: int = 0,
    recv_binding: Optional[str] = None
) -> AllocEvent:
    """Create an allocation event.
    
    Args:
        alloc_id: Allocation site ID
        target: Target variable
        type_: Type of allocation (obj, list, tuple, dict, set, func, class, etc.)
        bb: Basic block identifier
        idx: Index within basic block
        recv_binding: Receiver binding for method allocations
        
    Returns:
        Allocation event
    """
    return AllocEvent(
        kind="alloc",
        alloc_id=alloc_id,
        target=target,
        type=type_,
        recv_binding=recv_binding,
        bb=bb,
        idx=idx
    )


def make_call_event(
    call_id: str,
    bb: str = "bb0",
    idx: int = 0,
    callee_expr: Optional[str] = None,
    callee_symbol: Optional[str] = None,
    args: Optional[List[str]] = None,
    kwargs: Optional[Dict[str, str]] = None,
    receiver: Optional[str] = None,
    target: Optional[str] = None
) -> CallEvent:
    """Create a call event.
    
    Args:
        call_id: Call site ID
        bb: Basic block identifier
        idx: Index within basic block
        callee_expr: Callee expression string
        callee_symbol: Callee symbol name
        args: Argument variables
        kwargs: Keyword argument mappings
        receiver: Receiver object for method calls
        target: Target variable for return value
        
    Returns:
        Call event
    """
    return CallEvent(
        kind="call",
        call_id=call_id,
        callee_expr=callee_expr,
        callee_symbol=callee_symbol,
        args=args or [],
        kwargs=kwargs or {},
        receiver=receiver,
        target=target,
        bb=bb,
        idx=idx
    )


# Integration notes and TODOs

"""
Integration with PythonStAn IR:

1. Import the required IR/TAC modules:
   from pythonstan.ir.ir_statements import *
   from pythonstan.analysis.transform.ir import *
   from pythonstan.analysis.transform.three_address import *

2. Implement iter_function_events to handle specific IR node types:
   - IRAssign with different RHS types (Object, List, etc.)
   - IRLoadAttr, IRStoreAttr  
   - IRLoadSubscr, IRStoreSubscr
   - IRCall with different call types
   - IRReturn, IRCopy
   - IRRaise, IRCatchException

3. Implement site_id_of to extract source location information:
   - Use node.lineno, node.col_offset if available
   - Generate stable hash-based IDs for nodes without location info
   - Include file path information from the analysis context

4. Handle TAC (Three-Address Code) representation:
   - TAC may have different node structure than IR
   - Ensure consistent event generation for both representations
   - Consider instruction indices for ordering within basic blocks

5. Error handling:
   - Gracefully handle missing source location information
   - Log warnings for unrecognized IR node types
   - Provide fallback site IDs to maintain analysis stability
"""