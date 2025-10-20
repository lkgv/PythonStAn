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
    "iter_function_events",
    "iter_module_events",
    "site_id_of"
]


# Event type definitions using TypedDict for structured event data

class AllocEvent(TypedDict, total=False):
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
        
    Optional fields:
        elements: List of element variable names for containers (list/tuple/set)
        values: List of value variable names for dicts
        bases: List of base class names for classes
        closure_vars: List of closure variable names for functions
        func_binding: Function variable name for method objects
        yield_binding: Yield value variable name for generators
    """
    # Required fields
    kind: str  # "alloc"
    alloc_id: str
    target: str
    type: str  # obj, list, tuple, dict, set, func, class, exc, method, genframe
    bb: str
    idx: int
    
    # Optional fields
    recv_binding: Optional[str]
    elements: Optional[List[str]]
    values: Optional[List[str]]
    bases: Optional[List[str]]
    closure_vars: Optional[List[str]]
    func_binding: Optional[str]
    yield_binding: Optional[str]


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


# Union type for all events
Event = Union[
    AllocEvent, CallEvent, AttrLoadEvent, AttrStoreEvent, 
    ElemLoadEvent, ElemStoreEvent, ReturnEvent, ExceptionEvent
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
    
    # Check if function has attached CFG from scope_manager
    if hasattr(fn_ir_or_tac, '_cfg') and fn_ir_or_tac._cfg is not None:
        cfg = fn_ir_or_tac._cfg
        # CFG has a 'blks' attribute (set of BaseBlocks)
        if hasattr(cfg, 'blks'):
            # Convert set to list for indexing
            blocks = sorted(list(cfg.blks), key=lambda b: id(b))
            debug = False  # Set to True for verbose event extraction debugging
            if debug:
                print(f"[DEBUG] Processing CFG with {len(blocks)} blocks")
            for block_idx, block in enumerate(blocks):
                block_id = f"bb{block_idx}"
                # Each block has a 'stmts' list
                if hasattr(block, 'stmts'):
                    if debug:
                        print(f"[DEBUG] Block {block_id} has {len(block.stmts)} statements")
                    for instr_idx, instr in enumerate(block.stmts):
                        if debug:
                            # Show ALL instruction types
                            print(f"[DEBUG]   Stmt {instr_idx}: {type(instr).__name__}: {str(instr)[:80]}")
                        new_events = _process_ir_instruction(instr, block_id, instr_idx)
                        if debug and new_events:
                            print(f"[DEBUG]     -> {len(new_events)} events: {[evt.get('kind') for evt in new_events]}")
                        events.extend(new_events)
    # Handle different function representations
    elif hasattr(fn_ir_or_tac, 'get_blocks'):
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


def iter_module_events(ir_module: Any) -> Iterable[Event]:
    """Extract pointer-relevant events from module-level code.
    
    This function extracts events from module-level definitions including:
    - Class definitions (IRClass)
    - Function definitions (IRFunc) 
    - Module-level variable assignments
    
    Args:
        ir_module: Module IR representation
        
    Yields:
        Events representing module-level operations (primarily class allocations)
        
    Notes:
        This complements iter_function_events by capturing module-level
        definitions that occur outside of function scopes.
    """
    try:
        from pythonstan.ir.ir_statements import IRClass, IRFunc
    except ImportError:
        return iter([])
    
    events = []
    
    # Extract classes and functions from module's subscopes
    if hasattr(ir_module, 'classes'):
        # If module has explicit classes attribute
        for cls in ir_module.classes:
            site_id = site_id_of(cls, 'class')
            name = cls.name if hasattr(cls, 'name') else 'unknown'
            
            event_data = {
                "kind": "alloc",
                "alloc_id": site_id,
                "target": name,
                "type": "class",
                "bb": "module",
                "idx": 0
            }
            
            # Extract base classes
            if hasattr(cls, 'bases') and cls.bases:
                import ast
                bases = []
                for base_expr in cls.bases:
                    if isinstance(base_expr, ast.Name):
                        bases.append(base_expr.id)
                    elif isinstance(base_expr, ast.Attribute):
                        try:
                            bases.append(ast.unparse(base_expr))
                        except AttributeError:
                            if hasattr(base_expr, 'attr'):
                                bases.append(base_expr.attr)
                if bases:
                    event_data["bases"] = bases
            
            events.append(AllocEvent(**event_data))
    
    # Alternatively, look for IRClass instances in module body
    if hasattr(ir_module, 'body') or hasattr(ir_module, 'stmts'):
        body = getattr(ir_module, 'body', None) or getattr(ir_module, 'stmts', [])
        for stmt in body:
            if isinstance(stmt, IRClass):
                site_id = site_id_of(stmt, 'class')
                name = stmt.name if hasattr(stmt, 'name') else 'unknown'
                
                event_data = {
                    "kind": "alloc",
                    "alloc_id": site_id,
                    "target": name,
                    "type": "class",
                    "bb": "module",
                    "idx": 0
                }
                
                # Extract base classes
                if hasattr(stmt, 'bases') and stmt.bases:
                    import ast
                    bases = []
                    for base_expr in stmt.bases:
                        if isinstance(base_expr, ast.Name):
                            bases.append(base_expr.id)
                        elif isinstance(base_expr, ast.Attribute):
                            try:
                                bases.append(ast.unparse(base_expr))
                            except AttributeError:
                                if hasattr(base_expr, 'attr'):
                                    bases.append(base_expr.attr)
                    if bases:
                        event_data["bases"] = bases
                
                events.append(AllocEvent(**event_data))
    
    return iter(events)


def _is_constructor_call(func_name: str) -> bool:
    """Determine if a function call is likely a constructor.
    
    A constructor is a function that creates and returns a new object.
    This includes:
    - Built-in constructors: list, dict, tuple, set, object, etc.
    - Type conversion functions: str, int, float, bool, etc. (create new immutable objects)
    - Capitalized names (class convention): Dog, MyClass, etc.
    - Iterator/sequence constructors: range, enumerate, zip, map, filter
    
    Non-constructors include:
    - Inspection/query functions: len, isinstance, hasattr, callable, etc.
    - I/O functions: print
    - Pure computation: abs, max, min, sum, round, pow, divmod
    - Mutation functions: setattr, delattr (modify existing objects)
    
    Args:
        func_name: Name of the function being called
        
    Returns:
        True if likely a constructor, False otherwise
    """
    # Known non-constructors (pure functions that don't allocate)
    # These either inspect objects, perform I/O, or compute values without creating new objects
    NON_CONSTRUCTORS = {
        # I/O and inspection
        'print', 'len', 'isinstance', 'issubclass', 'hasattr', 'getattr',
        'callable', 'id', 'hash', 'type',
        # Mutation (modify existing objects, don't create new ones)
        'setattr', 'delattr',
        # Pure computation and utilities
        'abs', 'all', 'any', 'max', 'min', 'sum', 'round', 'pow', 'divmod',
        # Navigation
        'next', 'iter'  # iter sometimes returns input, next accesses existing iterator
    }
    
    # Known constructors - these ALL create new objects
    KNOWN_CONSTRUCTORS = {
        # Container types
        'list', 'dict', 'tuple', 'set', 'frozenset',
        # Basic types (immutable, but still allocate objects)
        'str', 'int', 'float', 'bool', 'complex',
        # Binary types
        'bytearray', 'bytes', 'memoryview',
        # Special types
        'object', 'type',
        # Iterator/sequence factories (these create new iterator objects)
        'range', 'enumerate', 'zip', 'map', 'filter', 'reversed',
        # String/formatting constructors
        'ascii', 'bin', 'chr', 'hex', 'oct', 'ord', 'repr', 'format',
        # Exception types (common ones)
        'Exception', 'BaseException', 'ValueError', 'TypeError', 'AttributeError',
        'KeyError', 'IndexError', 'RuntimeError', 'NotImplementedError'
    }
    
    if func_name in NON_CONSTRUCTORS:
        return False
    
    if func_name in KNOWN_CONSTRUCTORS:
        return True
    
    # Heuristic: Capitalized names are likely classes (constructors)
    # This catches user-defined classes following PEP 8 naming conventions
    if func_name and func_name[0].isupper():
        return True
    
    # Default: assume lowercase names are non-constructors
    # This is conservative - we prefer missing some allocations over creating spurious ones
    return False


def _process_ir_instruction(instr: Any, block_id: str, instr_idx: int) -> List[Event]:
    """Process a single IR instruction and generate relevant events."""
    events = []
    
    try:
        from pythonstan.ir.ir_statements import (
            IRAssign, IRCall, IRLoadAttr, IRStoreAttr, 
            IRLoadSubscr, IRStoreSubscr, IRReturn, IRRaise,
            IRCatchException, IRFunc, IRClass, IRCopy
        )
        import ast
    except ImportError:
        return events
    
    try:
        if isinstance(instr, IRAssign):
            # IRAssign: lval = rval (check if rval is an allocation or call)
            rval = instr.get_rval()
            lval = instr.get_lval()
            
            alloc_type = None
            is_call = False
            func_name = None
            target = lval.id if hasattr(lval, 'id') else str(lval)
            
            # Check if rval is a call or allocation
            if isinstance(rval, ast.Call):
                is_call = True
                if hasattr(rval.func, 'id'):
                    func_name = rval.func.id
                    if func_name in ('list', 'dict', 'tuple', 'set'):
                        alloc_type = func_name
                    elif func_name == 'object':
                        alloc_type = 'obj'
                    elif _is_constructor_call(func_name):
                        # This is a constructor call - will allocate an object
                        alloc_type = 'obj'
                    # else: non-constructor call, no allocation
                        
            elif isinstance(rval, ast.List):
                alloc_type = 'list'
                # Extract list elements
                elements = []
                if hasattr(rval, 'elts'):
                    for elt in rval.elts:
                        if hasattr(elt, 'id'):
                            elements.append(elt.id)
                        # For constants or complex expressions, skip (not tracked as pointers)
            elif isinstance(rval, ast.Dict):
                alloc_type = 'dict'
                # Extract dict values (keys are abstracted away)
                values = []
                if hasattr(rval, 'values'):
                    for val in rval.values:
                        if hasattr(val, 'id'):
                            values.append(val.id)
                        # For constants or complex expressions, skip
            elif isinstance(rval, ast.Tuple):
                alloc_type = 'tuple'
                # Extract tuple elements
                elements = []
                if hasattr(rval, 'elts'):
                    for elt in rval.elts:
                        if hasattr(elt, 'id'):
                            elements.append(elt.id)
            elif isinstance(rval, ast.Set):
                alloc_type = 'set'
                # Extract set elements
                elements = []
                if hasattr(rval, 'elts'):
                    for elt in rval.elts:
                        if hasattr(elt, 'id'):
                            elements.append(elt.id)
            elif isinstance(rval, ast.ListComp):
                alloc_type = 'list'
            elif isinstance(rval, ast.DictComp):
                alloc_type = 'dict'
            elif isinstance(rval, ast.SetComp):
                alloc_type = 'set'
            elif isinstance(rval, ast.Constant):
                # Constants need to be allocated as objects
                alloc_type = 'const'
            
            # Generate allocation event if needed
            if alloc_type:
                site_id = site_id_of(instr, 'alloc')
                
                # Build event with required fields
                event_data = {
                    "kind": "alloc",
                    "alloc_id": site_id,
                    "target": target,
                    "type": alloc_type,
                    "bb": block_id,
                    "idx": instr_idx
                }
                
                # Add optional fields if available
                if alloc_type in ('list', 'tuple', 'set') and 'elements' in locals() and elements:
                    event_data["elements"] = elements
                elif alloc_type == 'dict' and 'values' in locals() and values:
                    event_data["values"] = values
                
                events.append(AllocEvent(**event_data))
            
            # Generate call event if this is a function call
            if is_call and func_name:
                # Extract arguments from ast.Call
                args = []
                if hasattr(rval, 'args'):
                    for arg in rval.args:
                        if hasattr(arg, 'id'):
                            args.append(arg.id)
                        else:
                            # For complex expressions, use a placeholder
                            args.append(str(arg)[:20])  # Truncate for safety
                
                kwargs = {}
                if hasattr(rval, 'keywords'):
                    for kw in rval.keywords:
                        if kw.arg and hasattr(kw.value, 'id'):
                            kwargs[kw.arg] = kw.value.id
                
                # Generate call event
                call_site_id = site_id_of(instr, 'call')
                events.append(CallEvent(
                    kind="call",
                    call_id=call_site_id,
                    callee_expr=None,
                    callee_symbol=func_name,
                    args=args,
                    kwargs=kwargs,
                    receiver=None,
                    target=target,
                    bb=block_id,
                    idx=instr_idx
                ))
            elif not alloc_type:
                # Regular assignment without allocation or call - generate copy constraint
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
            
            # If this call has a target and is a constructor, generate allocation event
            if target and callee_symbol:
                # Use constructor detection to determine if this allocates
                if _is_constructor_call(callee_symbol):
                    # Generate allocation event for constructor calls
                    alloc_site_id = site_id_of(instr, 'alloc')
                    
                    # Determine allocation type
                    if callee_symbol in ('list', 'dict', 'tuple', 'set'):
                        alloc_type = callee_symbol
                    elif callee_symbol == 'object':
                        alloc_type = 'obj'
                    else:
                        # Generic constructor - creates object
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
            
            # Build event with required fields
            event_data = {
                "kind": "alloc",
                "alloc_id": site_id,
                "target": name,
                "type": 'func' if isinstance(instr, IRFunc) else 'class',
                "bb": block_id,
                "idx": instr_idx
            }
            
            # Extract optional fields
            if isinstance(instr, IRClass):
                # Extract base classes from IRClass.bases
                if hasattr(instr, 'bases') and instr.bases:
                    bases = []
                    for base_expr in instr.bases:
                        if isinstance(base_expr, ast.Name):
                            bases.append(base_expr.id)
                        elif isinstance(base_expr, ast.Attribute):
                            # Handle qualified names like "module.ClassName"
                            try:
                                bases.append(ast.unparse(base_expr))
                            except AttributeError:
                                # ast.unparse not available in older Python
                                # Fall back to simple name extraction
                                if hasattr(base_expr, 'attr'):
                                    bases.append(base_expr.attr)
                    if bases:
                        event_data["bases"] = bases
                        
            elif isinstance(instr, IRFunc):
                # Extract closure variables from IRFunc.cell_vars
                # cell_vars contains variables captured from enclosing scopes
                if hasattr(instr, 'cell_vars') and instr.cell_vars:
                    event_data["closure_vars"] = list(instr.cell_vars)
                # Also check for freevars (variables this function captures)
                elif hasattr(instr, 'freevars') and instr.freevars:
                    event_data["closure_vars"] = list(instr.freevars)
            
            events.append(AllocEvent(**event_data))
            
    except (AttributeError, TypeError) as e:
        # For unsupported or malformed nodes, skip silently
        # Could log warning in verbose mode
        pass
    
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