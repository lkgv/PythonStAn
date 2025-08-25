"""Type definitions for async facts in k-CFA pointer analysis.

This module defines TypedDict schemas for async-related facts collected by the
AsyncFactsHelper. These fact records provide a structured representation of
async objects and relations for downstream modeling and queries.

The schemas follow the async-event-schema.md specification and support
machine-readable export formats (JSONL/CSV).
"""

from typing import TypedDict, List, Optional, Literal, Union, Dict, Any
from typing_extensions import NotRequired


__all__ = [
    "CoroutineDefFact",
    "AwaitEdgeFact", 
    "TaskCreateFact",
    "TaskStateFact",
    "FutureFact",
    "QueueAllocFact",
    "QueuePutFact",
    "QueueGetFact", 
    "SyncAllocFact",
    "SyncOpFact",
    "LoopCallbackScheduleFact",
    "CallbackEdgeFact",
    "StreamFact",
    "AsyncFact"
]


class CoroutineDefFact(TypedDict):
    """Coroutine function definition fact.
    
    Records: async def functions and async generator functions
    
    Fields:
        fact_type: Always "coroutine_def"
        func_sym: Function symbol name (e.g., "module.func_name")
        def_site: Definition site ID (file:line:col:func)
        is_async: True for async def functions
        is_async_gen: True if function contains yield (async generator)
    """
    fact_type: Literal["coroutine_def"]
    func_sym: str
    def_site: str
    is_async: bool
    is_async_gen: bool


class AwaitEdgeFact(TypedDict):
    """Await expression fact.
    
    Records: await expressions and their targets
    
    Fields:
        fact_type: Always "await_edge" 
        await_id: Await site ID (file:line:col:await)
        awaiter_fn: Function containing the await expression
        awaited_targets: List of function symbols or allocation IDs being awaited
        may_unknown: True if awaited expression cannot be resolved statically
    """
    fact_type: Literal["await_edge"]
    await_id: str
    awaiter_fn: str
    awaited_targets: List[str]
    may_unknown: bool


class TaskCreateFact(TypedDict):
    """Task creation fact.
    
    Records: asyncio.create_task() and Task() constructor calls
    
    Fields:
        fact_type: Always "task_create"
        task_id: Task creation site ID (file:line:col:create_task)
        creator_fn: Function containing the task creation
        targets: List of coroutine function symbols passed to create_task
        args_vars: List of variable names passed as arguments
        may_unknown: True if coroutine argument cannot be resolved statically
    """
    fact_type: Literal["task_create"]
    task_id: str
    creator_fn: str
    targets: List[str]
    args_vars: List[str]
    may_unknown: bool


class TaskStateFact(TypedDict):
    """Task state operation fact.
    
    Records: Task methods like cancel(), done(), result(), exception()
    
    Fields:
        fact_type: Always "task_state"
        site_id: Operation site ID (file:line:col:task_op)
        op: Task operation name (cancel, done, result, exception, etc.)
        task_ids: List of task allocation IDs or variable names
    """
    fact_type: Literal["task_state"]
    site_id: str
    op: str
    task_ids: List[str]


class FutureFact(TypedDict):
    """Future object allocation fact.
    
    Records: Future() constructor calls and future-returning operations
    
    Fields:
        fact_type: Always "future"
        fut_id: Future allocation site ID (file:line:col:future)
        alloc_site: Allocation site identifier
        created_in_fn: Function containing the future allocation
    """
    fact_type: Literal["future"]
    fut_id: str
    alloc_site: str
    created_in_fn: str


class QueueAllocFact(TypedDict):
    """Queue allocation fact.
    
    Records: asyncio.Queue, LifoQueue, PriorityQueue constructor calls
    
    Fields:
        fact_type: Always "queue_alloc"
        queue_id: Queue allocation site ID (file:line:col:queue)
        queue_kind: Type of queue (Queue, LifoQueue, PriorityQueue)
        maxsize: Maximum queue size (0 for unbounded, -1 for unknown)
        alloc_ctx: Allocation context identifier
    """
    fact_type: Literal["queue_alloc"]
    queue_id: str
    queue_kind: str
    maxsize: int
    alloc_ctx: str


class QueuePutFact(TypedDict):
    """Queue put operation fact.
    
    Records: queue.put() and queue.put_nowait() calls
    
    Fields:
        fact_type: Always "queue_put"
        site_id: Put operation site ID (file:line:col:queue_put)
        queue_ids: List of queue allocation IDs being accessed
        value_vars: List of variable names being put into queue
    """
    fact_type: Literal["queue_put"]
    site_id: str
    queue_ids: List[str]
    value_vars: List[str]


class QueueGetFact(TypedDict):
    """Queue get operation fact.
    
    Records: queue.get() and queue.get_nowait() calls
    
    Fields:
        fact_type: Always "queue_get"
        site_id: Get operation site ID (file:line:col:queue_get)
        queue_ids: List of queue allocation IDs being accessed
        target_var: Variable name receiving the queue item
    """
    fact_type: Literal["queue_get"]
    site_id: str
    queue_ids: List[str]
    target_var: str


class SyncAllocFact(TypedDict):
    """Synchronization primitive allocation fact.
    
    Records: Lock, Semaphore, Event, Condition constructor calls
    
    Fields:
        fact_type: Always "sync_alloc"
        sync_id: Sync object allocation site ID (file:line:col:sync)
        kind: Type of sync primitive (Lock, Semaphore, BoundedSemaphore, Event, Condition)
    """
    fact_type: Literal["sync_alloc"]
    sync_id: str
    kind: str


class SyncOpFact(TypedDict):
    """Synchronization operation fact.
    
    Records: acquire(), release(), wait(), set(), clear() on sync primitives
    
    Fields:
        fact_type: Always "sync_op"
        site_id: Operation site ID (file:line:col:sync_op)
        kind: Type of sync primitive (Lock, Semaphore, Event, Condition)
        op: Operation name (acquire, release, wait, set, clear, etc.)
        target_ids: List of sync object allocation IDs
    """
    fact_type: Literal["sync_op"]
    site_id: str
    kind: str
    op: str
    target_ids: List[str]


class LoopCallbackScheduleFact(TypedDict):
    """Event loop callback scheduling fact.
    
    Records: loop.call_soon(), call_later(), call_at() calls
    
    Fields:
        fact_type: Always "loop_cb_schedule"
        cb_id: Callback scheduling site ID (file:line:col:loop_cb)
        api: Loop API used (call_soon, call_later, call_at)
        delay: Delay in seconds for call_later/call_at (None for call_soon)
        callback_targets: List of function symbols being scheduled
        args_vars: List of variable names passed as callback arguments
    """
    fact_type: Literal["loop_cb_schedule"]
    cb_id: str
    api: str
    delay: Optional[float]
    callback_targets: List[str]
    args_vars: List[str]


class CallbackEdgeFact(TypedDict):
    """Callback invocation edge fact.
    
    Records: potential callback invocations from event loop
    
    Fields:
        fact_type: Always "callback_edge"
        cb_id: Callback edge ID (file:line:col:callback)
        caller_fn: Function that scheduled the callback (e.g., event loop)
        callee_targets: List of function symbols that may be invoked
    """
    fact_type: Literal["callback_edge"]
    cb_id: str
    caller_fn: str
    callee_targets: List[str]


class StreamFact(TypedDict):
    """Async stream operation fact.
    
    Records: asyncio.open_connection(), start_server() calls
    
    Fields:
        fact_type: Always "stream"
        site_id: Stream operation site ID (file:line:col:stream)
        api: Stream API used (open_connection, start_server)
        reader_var: Variable name for StreamReader (optional)
        writer_var: Variable name for StreamWriter (optional)
    """
    fact_type: Literal["stream"]
    site_id: str
    api: str
    reader_var: Optional[str]
    writer_var: Optional[str]


# Union type for all async facts
AsyncFact = Union[
    CoroutineDefFact,
    AwaitEdgeFact,
    TaskCreateFact,
    TaskStateFact,
    FutureFact,
    QueueAllocFact,
    QueuePutFact,
    QueueGetFact,
    SyncAllocFact,
    SyncOpFact,
    LoopCallbackScheduleFact,
    CallbackEdgeFact,
    StreamFact
]


# Type aliases for common patterns
FactCollection = Dict[str, List[AsyncFact]]
SiteId = str
FuncSymbol = str
AllocId = str
VarName = str
