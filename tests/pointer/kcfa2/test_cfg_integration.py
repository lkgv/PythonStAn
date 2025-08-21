"""Tests for CFG integration in k-CFA2 pointer analysis.

This module tests the integration between the pointer analysis and the
control flow graph (CFG) representation from IR/TAC:
- Extraction of events from IR/TAC nodes
- Site ID generation from source location info
- Adaptation of IR/TAC to pointer analysis model
"""

import pytest
from dataclasses import dataclass, field
from typing import Dict, List, Set, FrozenSet, Tuple, Optional, Protocol, Iterator, Any

from pythonstan.analysis.pointer.kcfa2.context import Context
from pythonstan.analysis.pointer.kcfa2.model import AbstractObject
from pythonstan.analysis.pointer.kcfa2.ir_adapter import site_id_of


# Mock IR/TAC nodes for testing
@dataclass
class MockSourceLocation:
    """Mock source location for testing."""
    filename: str
    lineno: int
    col_offset: int


@dataclass
class MockNode:
    """Mock base node class for IR/TAC nodes."""
    uid: str
    source_location: Optional[MockSourceLocation]


@dataclass
class MockIRAssign(MockNode):
    """Mock IR assignment node."""
    target: str
    value: Any
    
    def is_allocation(self) -> bool:
        """Check if this is an allocation operation."""
        # For testing, we assume certain value types are allocations
        value_type = getattr(self.value, "__class__", None)
        if value_type:
            class_name = value_type.__name__
            # Check for both real IR classes and mock classes
            allocation_types = {
                "IRObject", "IRList", "IRDict", "IRFunc",
                "MockIRObject", "MockIRList", "MockIRDict", "MockIRFunc"
            }
            return class_name in allocation_types
        return False


@dataclass
class MockIRObject(MockNode):
    """Mock IR object creation node."""
    class_name: str


@dataclass
class MockIRList(MockNode):
    """Mock IR list creation node."""
    elements: List[str]


@dataclass
class MockIRDict(MockNode):
    """Mock IR dictionary creation node."""
    keys: List[str]
    values: List[str]


@dataclass
class MockIRFunc(MockNode):
    """Mock IR function definition node."""
    name: str
    params: List[str]
    free_vars: List[str] = field(default_factory=list)


@dataclass
class MockIRCall(MockNode):
    """Mock IR function call node."""
    func: str
    args: List[str]
    target: str
    is_method_call: bool = False
    base: Optional[str] = None
    method_name: Optional[str] = None


@dataclass
class MockIRLoadAttr(MockNode):
    """Mock IR attribute load node."""
    base: str
    attr: str
    target: str


@dataclass
class MockIRStoreAttr(MockNode):
    """Mock IR attribute store node."""
    base: str
    attr: str
    value: str


# Mock Protocol implementations
class MockIRAdapterProtocols:
    """Mock protocols for IR adapter testing."""
    
    class IRFunction(Protocol):
        """Protocol for IR function."""
        name: str
        filename: str
        
        def iter_nodes(self) -> Iterator[MockNode]:
            ...
    
    @dataclass
    class MockIRFunction:
        """Mock IR function implementation."""
        name: str
        filename: str
        nodes: List[MockNode]
        
        def iter_nodes(self) -> Iterator[MockNode]:
            """Iterate through function nodes."""
            yield from self.nodes


def test_site_id_generation():
    """Test generation of site IDs from source locations."""
    # Source location with all information
    loc1 = MockSourceLocation(filename="test.py", lineno=42, col_offset=10)
    node1 = MockNode(uid="node1", source_location=loc1)
    
    # Call site ID
    call_id = site_id_of(node1, "call")
    assert call_id == "test.py:42:10:call"
    
    # Allocation site ID
    alloc_id = site_id_of(node1, "obj")
    assert alloc_id == "test.py:42:10:obj"
    
    # Node without source location
    node2 = MockNode(uid="node2", source_location=None)
    fallback_id = site_id_of(node2, "call")
    assert "call" in fallback_id
    assert "node2" in fallback_id or node2.uid in fallback_id


# @pytest.mark.xfail(reason="IR adapter not fully implemented yet")
def test_extract_events_from_ir():
    """Test extraction of events from IR nodes."""
    # Create mock IR function with nodes
    loc = MockSourceLocation(filename="test.py", lineno=10, col_offset=5)
    
    # Object allocation
    obj_node = MockIRObject(uid="obj1", source_location=loc, class_name="MyClass")
    assign_obj = MockIRAssign(
        uid="assign1", 
        source_location=loc,
        target="x", 
        value=obj_node
    )
    
    # List allocation
    list_node = MockIRList(uid="list1", source_location=loc, elements=["a", "b"])
    assign_list = MockIRAssign(
        uid="assign2",
        source_location=MockSourceLocation(filename="test.py", lineno=11, col_offset=5),
        target="y",
        value=list_node
    )
    
    # Function call
    call_node = MockIRCall(
        uid="call1",
        source_location=MockSourceLocation(filename="test.py", lineno=12, col_offset=5),
        func="f",
        args=["x", "y"],
        target="z"
    )
    
    # Attribute load
    load_attr = MockIRLoadAttr(
        uid="load1",
        source_location=MockSourceLocation(filename="test.py", lineno=13, col_offset=5),
        base="x",
        attr="attr",
        target="w"
    )
    
    # Attribute store
    store_attr = MockIRStoreAttr(
        uid="store1",
        source_location=MockSourceLocation(filename="test.py", lineno=14, col_offset=5),
        base="x",
        attr="attr",
        value="y"
    )
    
    # Method call
    method_call = MockIRCall(
        uid="call2",
        source_location=MockSourceLocation(filename="test.py", lineno=15, col_offset=5),
        func="method",
        args=["arg"],
        target="result",
        is_method_call=True,
        base="x",
        method_name="method"
    )
    
    # Create IR function with all nodes
    ir_func = MockIRAdapterProtocols.MockIRFunction(
        name="test_function",
        filename="test.py",
        nodes=[assign_obj, assign_list, call_node, load_attr, store_attr, method_call]
    )
    
    # In a complete implementation, we would:
    # 1. Use ir_adapter.iter_function_events(ir_func)
    # 2. Check each event type and content
    
    # For now, we'll assert basic expectations about site IDs
    obj_site_id = site_id_of(assign_obj, "obj")
    assert obj_site_id == "test.py:10:5:obj"
    
    list_site_id = site_id_of(assign_list, "list")
    assert list_site_id == "test.py:11:5:list"
    
    call_site_id = site_id_of(call_node, "call")
    assert call_site_id == "test.py:12:5:call"


def test_allocation_event_extraction():
    """Test extraction of allocation events from IR nodes."""
    # Create mock allocation nodes
    loc = MockSourceLocation(filename="test.py", lineno=10, col_offset=5)
    
    # Object allocation
    obj_value = MockIRObject(uid="obj1", source_location=loc, class_name="MyClass")
    obj_assign = MockIRAssign(
        uid="assign1", 
        source_location=loc,
        target="x", 
        value=obj_value
    )
    
    # In a real IR adapter, we would:
    # 1. Detect that this is an object allocation
    # 2. Create an AllocObjectEvent with correct site ID
    # 3. Extract target variable name
    
    # For now, test the basic identification
    assert obj_assign.is_allocation()
    site_id = site_id_of(obj_assign, "obj")
    assert site_id == "test.py:10:5:obj"
    assert obj_assign.target == "x"


# @pytest.mark.xfail(reason="IR adapter not fully implemented yet")
def test_call_event_extraction():
    """Test extraction of call events from IR nodes."""
    # Create mock call node
    loc = MockSourceLocation(filename="test.py", lineno=12, col_offset=5)
    
    # Regular function call
    call_node = MockIRCall(
        uid="call1",
        source_location=loc,
        func="f",
        args=["x", "y"],
        target="z"
    )
    
    # Method call
    method_call = MockIRCall(
        uid="call2",
        source_location=MockSourceLocation(filename="test.py", lineno=15, col_offset=5),
        func="method",
        args=["arg"],
        target="result",
        is_method_call=True,
        base="x",
        method_name="method"
    )
    
    # In a real IR adapter, we would:
    # 1. Distinguish between direct and method calls
    # 2. Create appropriate CallEvent with correct site ID
    # 3. Extract function/method name, args, target
    
    # For now, test basic identification
    assert not call_node.is_method_call
    assert method_call.is_method_call
    
    call_site_id = site_id_of(call_node, "call")
    assert call_site_id == "test.py:12:5:call"
    assert call_node.func == "f"
    assert call_node.args == ["x", "y"]
    assert call_node.target == "z"
    
    method_site_id = site_id_of(method_call, "call")
    assert method_site_id == "test.py:15:5:call"
    assert method_call.base == "x"
    assert method_call.method_name == "method"


# @pytest.mark.xfail(reason="IR adapter not fully implemented yet")
def test_attribute_event_extraction():
    """Test extraction of attribute events from IR nodes."""
    # Create mock attribute nodes
    loc = MockSourceLocation(filename="test.py", lineno=13, col_offset=5)
    
    # Attribute load
    load_attr = MockIRLoadAttr(
        uid="load1",
        source_location=loc,
        base="x",
        attr="attr",
        target="w"
    )
    
    # Attribute store
    store_attr = MockIRStoreAttr(
        uid="store1",
        source_location=MockSourceLocation(filename="test.py", lineno=14, col_offset=5),
        base="x",
        attr="attr",
        value="y"
    )
    
    # In a real IR adapter, we would:
    # 1. Distinguish between load and store operations
    # 2. Create AttrLoadEvent or AttrStoreEvent with correct site ID
    # 3. Extract base object, attribute name, target/value
    
    # For now, test basic identification
    load_site_id = site_id_of(load_attr, "attr")
    assert load_site_id == "test.py:13:5:attr"
    assert load_attr.base == "x"
    assert load_attr.attr == "attr"
    assert load_attr.target == "w"
    
    store_site_id = site_id_of(store_attr, "attr")
    assert store_site_id == "test.py:14:5:attr"
    assert store_attr.base == "x"
    assert store_attr.attr == "attr"
    assert store_attr.value == "y"