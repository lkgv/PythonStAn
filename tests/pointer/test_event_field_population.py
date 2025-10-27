"""Test that all event fields are populated correctly.

This test verifies that the IR adapter extracts and populates optional fields
in AllocEvent for containers, classes, functions, etc.
"""

import pytest
import sys
import ast


def test_container_elements_extraction():
    """Test that list/tuple/set elements are extracted."""
    from pythonstan.analysis.pointer.kcfa2.ir_adapter import _process_ir_instruction
    from pythonstan.ir.ir_statements import IRAssign
    
    # Create a mock IRAssign for a list literal
    # tmp_1 = [x, y, z]
    lval = ast.Name(id='tmp_1')
    rval = ast.List(elts=[
        ast.Name(id='x'),
        ast.Name(id='y'),
        ast.Name(id='z')
    ])
    
    # Create mock IRAssign
    class MockIRAssign:
        def get_lval(self):
            return lval
        def get_rval(self):
            return rval
    
    instr = MockIRAssign()
    events = _process_ir_instruction(instr, "bb0", 0)
    
    # Debug: print what events we got
    print(f"Events generated: {events}")
    
    # Should generate an allocation event with elements field
    alloc_events = [e for e in events if e.get("kind") == "alloc"]
    print(f"Alloc events: {alloc_events}")
    assert len(alloc_events) == 1, f"Expected 1 alloc event, got {len(alloc_events)}"
    
    event = alloc_events[0]
    assert event["type"] == "list"
    assert "elements" in event
    assert event["elements"] == ["x", "y", "z"]
    print("✓ List elements extracted correctly")


def test_dict_values_extraction():
    """Test that dict values are extracted."""
    from pythonstan.analysis.pointer.kcfa2.ir_adapter import _process_ir_instruction
    
    # Create a mock IRAssign for a dict literal
    # tmp_1 = {"a": x, "b": y}
    lval = ast.Name(id='tmp_1')
    rval = ast.Dict(
        keys=[ast.Constant(value="a"), ast.Constant(value="b")],
        values=[ast.Name(id='x'), ast.Name(id='y')]
    )
    
    class MockIRAssign:
        def get_lval(self):
            return lval
        def get_rval(self):
            return rval
    
    instr = MockIRAssign()
    events = _process_ir_instruction(instr, "bb0", 0)
    
    alloc_events = [e for e in events if e.get("kind") == "alloc"]
    assert len(alloc_events) == 1
    
    event = alloc_events[0]
    assert event["type"] == "dict"
    assert "values" in event
    assert event["values"] == ["x", "y"]
    print("✓ Dict values extracted correctly")


def test_class_bases_extraction():
    """Test that class base classes are extracted."""
    from pythonstan.analysis.pointer.kcfa2.ir_adapter import _process_ir_instruction
    from pythonstan.ir.ir_statements import IRClass
    
    # Create a mock IRClass with bases
    # class Dog(Animal, Mammal): pass
    class MockIRClass:
        name = "Dog"
        bases = [ast.Name(id="Animal"), ast.Name(id="Mammal")]
    
    instr = MockIRClass()
    events = _process_ir_instruction(instr, "bb0", 0)
    
    alloc_events = [e for e in events if e.get("kind") == "alloc"]
    assert len(alloc_events) == 1
    
    event = alloc_events[0]
    assert event["type"] == "class"
    assert event["target"] == "Dog"
    assert "bases" in event
    assert event["bases"] == ["Animal", "Mammal"]
    print("✓ Class bases extracted correctly")


def test_closure_vars_extraction():
    """Test that closure variables are extracted from functions."""
    from pythonstan.analysis.pointer.kcfa2.ir_adapter import _process_ir_instruction
    from pythonstan.ir.ir_statements import IRFunc
    
    # Create a mock IRFunc with closure vars
    # def inner(): return x + y  # captures x, y
    class MockIRFunc:
        name = "inner"
        cell_vars = {"x", "y"}
    
    instr = MockIRFunc()
    events = _process_ir_instruction(instr, "bb0", 0)
    
    alloc_events = [e for e in events if e.get("kind") == "alloc"]
    assert len(alloc_events) == 1
    
    event = alloc_events[0]
    assert event["type"] == "func"
    assert event["target"] == "inner"
    assert "closure_vars" in event
    assert set(event["closure_vars"]) == {"x", "y"}
    print("✓ Closure variables extracted correctly")


def test_tuple_elements_extraction():
    """Test that tuple elements are extracted."""
    from pythonstan.analysis.pointer.kcfa2.ir_adapter import _process_ir_instruction
    
    # Create a mock IRAssign for a tuple literal
    # tmp_1 = (a, b, c)
    lval = ast.Name(id='tmp_1')
    rval = ast.Tuple(elts=[
        ast.Name(id='a'),
        ast.Name(id='b'),
        ast.Name(id='c')
    ])
    
    class MockIRAssign:
        def get_lval(self):
            return lval
        def get_rval(self):
            return rval
    
    instr = MockIRAssign()
    events = _process_ir_instruction(instr, "bb0", 0)
    
    alloc_events = [e for e in events if e.get("kind") == "alloc"]
    assert len(alloc_events) == 1
    
    event = alloc_events[0]
    assert event["type"] == "tuple"
    assert "elements" in event
    assert event["elements"] == ["a", "b", "c"]
    print("✓ Tuple elements extracted correctly")


def test_set_elements_extraction():
    """Test that set elements are extracted."""
    from pythonstan.analysis.pointer.kcfa2.ir_adapter import _process_ir_instruction
    
    # Create a mock IRAssign for a set literal
    # tmp_1 = {x, y}
    lval = ast.Name(id='tmp_1')
    rval = ast.Set(elts=[
        ast.Name(id='x'),
        ast.Name(id='y')
    ])
    
    class MockIRAssign:
        def get_lval(self):
            return lval
        def get_rval(self):
            return rval
    
    instr = MockIRAssign()
    events = _process_ir_instruction(instr, "bb0", 0)
    
    alloc_events = [e for e in events if e.get("kind") == "alloc"]
    assert len(alloc_events) == 1
    
    event = alloc_events[0]
    assert event["type"] == "set"
    assert "elements" in event
    assert event["elements"] == ["x", "y"]
    print("✓ Set elements extracted correctly")


if __name__ == "__main__":
    print("\n=== Testing Event Field Population ===\n")
    
    try:
        test_container_elements_extraction()
        test_dict_values_extraction()
        test_class_bases_extraction()
        test_closure_vars_extraction()
        test_tuple_elements_extraction()
        test_set_elements_extraction()
        
        print("\n✅ All event field population tests passed!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

