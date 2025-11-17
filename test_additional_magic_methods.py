#!/usr/bin/env python3
"""Test additional magic methods implementation.

This test verifies the newly added magic method support:
- __enter__ / __exit__ for context managers
- __iter__ / __next__ for iterators
- __add__, __mul__, etc. for binary operators
"""

def test_context_manager_methods():
    """Test __enter__ and __exit__ magic method constraint generation."""
    from pythonstan.analysis.pointer.kcfa.ir_translator import IRTranslator
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.analysis.pointer.kcfa.variable import Variable, Scope, VariableKind
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext
    from pythonstan.analysis.pointer.kcfa.constraints import LoadConstraint, CallConstraint
    
    # Setup
    config = Config()
    translator = IRTranslator(config)
    scope = Scope(name="test", kind="function")
    context = CallStringContext(k=2)
    translator._current_scope = scope
    translator._current_context = context
    
    # Create test variables
    ctx_mgr_var = Variable("ctx_mgr", scope, context, VariableKind.LOCAL)
    target_var = Variable("target", scope, context, VariableKind.LOCAL)
    
    # Test __enter__
    print("[1/3] Testing __enter__ constraint generation...")
    enter_constraints = translator._translate_with_enter(ctx_mgr_var, target_var)
    assert len(enter_constraints) == 2, f"Expected 2 constraints, got {len(enter_constraints)}"
    assert isinstance(enter_constraints[0], LoadConstraint), "First should be LoadConstraint"
    assert isinstance(enter_constraints[1], CallConstraint), "Second should be CallConstraint"
    assert enter_constraints[0].field.name == "__enter__", "Should load __enter__"
    print("    ✓ __enter__ generates correct constraints")
    
    # Test __exit__
    print("[2/3] Testing __exit__ constraint generation...")
    exit_constraints = translator._translate_with_exit(ctx_mgr_var)
    assert len(exit_constraints) == 2, f"Expected 2 constraints, got {len(exit_constraints)}"
    assert isinstance(exit_constraints[0], LoadConstraint), "First should be LoadConstraint"
    assert isinstance(exit_constraints[1], CallConstraint), "Second should be CallConstraint"
    assert exit_constraints[0].field.name == "__exit__", "Should load __exit__"
    assert exit_constraints[1].target is None, "__exit__ should have no target"
    print("    ✓ __exit__ generates correct constraints")
    
    print("[3/3] Context manager methods complete!")
    return True


def test_iterator_methods():
    """Test __iter__ and __next__ magic method constraint generation."""
    from pythonstan.analysis.pointer.kcfa.ir_translator import IRTranslator
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.analysis.pointer.kcfa.variable import Variable, Scope, VariableKind
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext
    from pythonstan.analysis.pointer.kcfa.constraints import LoadConstraint, CallConstraint
    
    # Setup
    config = Config()
    translator = IRTranslator(config)
    scope = Scope(name="test", kind="function")
    context = CallStringContext(k=2)
    translator._current_scope = scope
    translator._current_context = context
    
    # Create test variables
    iterable_var = Variable("iterable", scope, context, VariableKind.LOCAL)
    iterator_var = Variable("iterator", scope, context, VariableKind.LOCAL)
    item_var = Variable("item", scope, context, VariableKind.LOCAL)
    
    # Test __iter__
    print("[1/3] Testing __iter__ constraint generation...")
    iter_constraints = translator._translate_iter(iterable_var, iterator_var)
    assert len(iter_constraints) == 2, f"Expected 2 constraints, got {len(iter_constraints)}"
    assert isinstance(iter_constraints[0], LoadConstraint), "First should be LoadConstraint"
    assert isinstance(iter_constraints[1], CallConstraint), "Second should be CallConstraint"
    assert iter_constraints[0].field.name == "__iter__", "Should load __iter__"
    assert iter_constraints[1].target == iterator_var, "Should set iterator target"
    print("    ✓ __iter__ generates correct constraints")
    
    # Test __next__
    print("[2/3] Testing __next__ constraint generation...")
    next_constraints = translator._translate_next(iterator_var, item_var)
    assert len(next_constraints) == 2, f"Expected 2 constraints, got {len(next_constraints)}"
    assert isinstance(next_constraints[0], LoadConstraint), "First should be LoadConstraint"
    assert isinstance(next_constraints[1], CallConstraint), "Second should be CallConstraint"
    assert next_constraints[0].field.name == "__next__", "Should load __next__"
    assert next_constraints[1].target == item_var, "Should set item target"
    print("    ✓ __next__ generates correct constraints")
    
    print("[3/3] Iterator methods complete!")
    return True


def test_binary_operator_methods():
    """Test binary operator magic method constraint generation."""
    from pythonstan.analysis.pointer.kcfa.ir_translator import IRTranslator
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.analysis.pointer.kcfa.variable import Variable, Scope, VariableKind
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext
    from pythonstan.analysis.pointer.kcfa.constraints import LoadConstraint, CallConstraint
    
    # Setup
    config = Config()
    translator = IRTranslator(config)
    scope = Scope(name="test", kind="function")
    context = CallStringContext(k=2)
    translator._current_scope = scope
    translator._current_context = context
    
    # Create test variables
    left_var = Variable("left", scope, context, VariableKind.LOCAL)
    right_var = Variable("right", scope, context, VariableKind.LOCAL)
    result_var = Variable("result", scope, context, VariableKind.LOCAL)
    
    # Test __add__
    print("[1/4] Testing __add__ constraint generation...")
    add_constraints = translator._translate_binary_op(left_var, right_var, result_var, "__add__")
    assert len(add_constraints) == 2, f"Expected 2 constraints, got {len(add_constraints)}"
    assert isinstance(add_constraints[0], LoadConstraint), "First should be LoadConstraint"
    assert isinstance(add_constraints[1], CallConstraint), "Second should be CallConstraint"
    assert add_constraints[0].field.name == "__add__", "Should load __add__"
    assert add_constraints[1].args == (right_var,), "Should pass right operand"
    assert add_constraints[1].target == result_var, "Should set result target"
    print("    ✓ __add__ generates correct constraints")
    
    # Test __mul__
    print("[2/4] Testing __mul__ constraint generation...")
    mul_constraints = translator._translate_binary_op(left_var, right_var, result_var, "__mul__")
    assert len(mul_constraints) == 2, f"Expected 2 constraints, got {len(mul_constraints)}"
    assert mul_constraints[0].field.name == "__mul__", "Should load __mul__"
    print("    ✓ __mul__ generates correct constraints")
    
    # Test __sub__
    print("[3/4] Testing __sub__ constraint generation...")
    sub_constraints = translator._translate_binary_op(left_var, right_var, result_var, "__sub__")
    assert len(sub_constraints) == 2, f"Expected 2 constraints, got {len(sub_constraints)}"
    assert sub_constraints[0].field.name == "__sub__", "Should load __sub__"
    print("    ✓ __sub__ generates correct constraints")
    
    print("[4/4] Binary operator methods complete!")
    return True


def main():
    """Run all tests."""
    print("=" * 70)
    print("Testing Additional Magic Methods Implementation")
    print("=" * 70)
    
    results = []
    
    try:
        print("\n### Context Manager Methods (__enter__, __exit__)")
        result1 = test_context_manager_methods()
        results.append(result1)
    except Exception as e:
        print(f"    ✗ Context manager tests failed: {e}")
        results.append(False)
    
    try:
        print("\n### Iterator Methods (__iter__, __next__)")
        result2 = test_iterator_methods()
        results.append(result2)
    except Exception as e:
        print(f"    ✗ Iterator tests failed: {e}")
        results.append(False)
    
    try:
        print("\n### Binary Operator Methods (__add__, __mul__, __sub__)")
        result3 = test_binary_operator_methods()
        results.append(result3)
    except Exception as e:
        print(f"    ✗ Binary operator tests failed: {e}")
        results.append(False)
    
    # Summary
    print("\n" + "=" * 70)
    passed = sum(results)
    total = len(results)
    
    if passed == total:
        print(f"✅ All {total} test groups passed!")
        print("\nAdditional magic methods are working correctly:")
        print("  • __enter__ / __exit__ for context managers")
        print("  • __iter__ / __next__ for iterators")
        print("  • __add__, __mul__, __sub__ and other binary operators")
        return 0
    else:
        print(f"❌ {total - passed} test group(s) failed")
        return 1


if __name__ == '__main__':
    import sys
    sys.exit(main())

