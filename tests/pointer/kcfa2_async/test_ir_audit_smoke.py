"""Smoke tests for async IR node presence.

This module verifies that the required async IR nodes (IRAwait, IRYield, IRFunc.is_async)
are present in the pythonstan.ir.ir_statements module as required for async analysis.

Tests will xfail with explicit reasons if async context manager or async iterator
IR nodes are missing, as these may require additional IR extensions.
"""

import pytest
import ast
from typing import List, Optional

from pythonstan.ir.ir_statements import (
    IRStatement, IRAwait, IRYield, IRFunc, IRModule
)


class TestAsyncIRNodesPresence:
    """Test presence of required async IR nodes."""
    
    def test_ir_await_exists(self):
        """Test that IRAwait class exists and has required interface."""
        # Verify IRAwait class exists
        assert IRAwait is not None
        assert issubclass(IRAwait, IRStatement)
        
        # Test basic IRAwait construction with mock AST
        await_stmt = ast.parse("result = await coro()").body[0]
        ir_await = IRAwait(await_stmt)
        
        # Verify required methods exist
        assert hasattr(ir_await, 'get_target')
        assert hasattr(ir_await, 'get_value')
        assert hasattr(ir_await, 'get_loads')
        assert hasattr(ir_await, 'get_stores')
        assert hasattr(ir_await, 'get_ast')
        
        # Test target and value extraction
        target = ir_await.get_target()
        assert target is not None
        assert isinstance(target, ast.Name)
        assert target.id == "result"
        
        value = ir_await.get_value()
        assert value is not None
        assert isinstance(value, ast.Call)
    
    def test_ir_yield_exists(self):
        """Test that IRYield class exists and has required interface."""
        # Verify IRYield class exists
        assert IRYield is not None
        assert issubclass(IRYield, IRStatement)
        
        # Test basic IRYield construction with mock AST
        yield_stmt = ast.parse("result = yield value").body[0]
        ir_yield = IRYield(yield_stmt)
        
        # Verify required methods exist
        assert hasattr(ir_yield, 'is_yield_from')
        assert hasattr(ir_yield, 'get_loads')
        assert hasattr(ir_yield, 'get_stores')
        assert hasattr(ir_yield, 'get_ast')
        
        # Test yield from detection
        assert not ir_yield.is_yield_from()
    
    def test_ir_func_async_support(self):
        """Test that IRFunc supports async function detection."""
        # Verify IRFunc has is_async attribute
        assert hasattr(IRFunc, '__init__')
        
        # Test with async function AST
        async_func_ast = ast.parse("async def test(): pass").body[0]
        assert isinstance(async_func_ast, ast.AsyncFunctionDef)
        
        ir_func = IRFunc("test.test", async_func_ast)
        
        # Verify is_async attribute exists and is True
        assert hasattr(ir_func, 'is_async')
        assert ir_func.is_async is True
        
        # Test with regular function
        sync_func_ast = ast.parse("def test(): pass").body[0]
        assert isinstance(sync_func_ast, ast.FunctionDef)
        
        ir_func_sync = IRFunc("test.test_sync", sync_func_ast)
        assert ir_func_sync.is_async is False
    
    def test_yield_from_support(self):
        """Test that IRYield supports yield from expressions."""
        # Test yield from construction
        yield_from_stmt = ast.parse("result = yield from generator()").body[0]
        ir_yield = IRYield(yield_from_stmt)
        
        # Verify yield from detection
        assert ir_yield.is_yield_from() is True
    
    def test_await_without_assignment(self):
        """Test IRAwait handles await without assignment."""
        # Test await expression without assignment  
        await_expr_stmt = ast.parse("await coro()").body[0]
        ir_await = IRAwait(await_expr_stmt)
        
        # Target should be None for expression statements
        target = ir_await.get_target()
        assert target is None
        
        # Value should still be extractable
        value = ir_await.get_value()
        assert value is not None
        assert isinstance(value, ast.Call)


class TestAsyncContextManagerIRNodes:
    """Test async context manager IR node support."""
    
    @pytest.mark.xfail(reason="AsyncWith IR node not yet implemented")
    def test_async_with_ir_node(self):
        """Test for async with (async context manager) IR node."""
        # This test is expected to fail until AsyncWith IR node is implemented
        from pythonstan.ir.ir_statements import IRAsyncWith
        
        # Test AsyncWith construction
        async_with_stmt = ast.parse("async with lock: pass").body[0]
        assert isinstance(async_with_stmt, ast.AsyncWith)
        
        ir_async_with = IRAsyncWith(async_with_stmt)
        assert hasattr(ir_async_with, 'get_context_manager')
        assert hasattr(ir_async_with, 'get_optional_vars')
    
    @pytest.mark.xfail(reason="AsyncFor IR node not yet implemented") 
    def test_async_for_ir_node(self):
        """Test for async for (async iterator) IR node."""
        # This test is expected to fail until AsyncFor IR node is implemented
        from pythonstan.ir.ir_statements import IRAsyncFor
        
        # Test AsyncFor construction
        async_for_stmt = ast.parse("async for item in async_iter(): pass").body[0]
        assert isinstance(async_for_stmt, ast.AsyncFor)
        
        ir_async_for = IRAsyncFor(async_for_stmt)
        assert hasattr(ir_async_for, 'get_target')
        assert hasattr(ir_async_for, 'get_iter')


class TestAsyncIRIntegration:
    """Test integration of async IR nodes."""
    
    def test_async_function_with_await(self):
        """Test async function containing await expressions."""
        code = """
async def test_func():
    result = await other_coro()
    return result
"""
        module_ast = ast.parse(code)
        func_ast = module_ast.body[0]
        
        # Create IRFunc
        ir_func = IRFunc("test.test_func", func_ast)
        assert ir_func.is_async is True
        
        # Verify we can create IRAwait from the await statement
        # (In real usage, this would be done by IR transformation)
        await_stmt = func_ast.body[0]  # result = await other_coro()
        ir_await = IRAwait(await_stmt)
        
        assert ir_await.get_target().id == "result"
        assert isinstance(ir_await.get_value(), ast.Call)
    
    def test_async_generator_detection(self):
        """Test async generator function (async def + yield) detection."""
        code = """
async def async_gen():
    yield 1
    yield 2
"""
        module_ast = ast.parse(code)
        func_ast = module_ast.body[0]
        
        # Create IRFunc
        ir_func = IRFunc("test.async_gen", func_ast)
        assert ir_func.is_async is True
        
        # Verify we can create IRYield from yield statements
        yield_stmt = func_ast.body[0]  # yield 1
        ir_yield = IRYield(yield_stmt)
        
        assert not ir_yield.is_yield_from()
        
        # In a complete implementation, we would check if the function
        # contains yield to determine if it's an async generator
        # This would be done at a higher level analysis
    
    def test_mixed_async_constructs(self):
        """Test function with both await and yield (async generator)."""
        code = """
async def mixed_async_gen():
    data = await fetch_data()
    yield data
    yield await fetch_more()
"""
        module_ast = ast.parse(code)
        func_ast = module_ast.body[0]
        
        ir_func = IRFunc("test.mixed_async_gen", func_ast)
        assert ir_func.is_async is True
        
        # Test first statement: data = await fetch_data()
        await_stmt = func_ast.body[0]
        ir_await = IRAwait(await_stmt)
        assert ir_await.get_target().id == "data"
        
        # Test second statement: yield data  
        yield_stmt = func_ast.body[1]
        ir_yield = IRYield(yield_stmt)
        assert not ir_yield.is_yield_from()
        
        # Test third statement: yield await fetch_more()
        # This is a complex case where yield contains await
        await_yield_stmt = func_ast.body[2]
        # The yield statement should be parseable
        ir_yield2 = IRYield(await_yield_stmt)
        assert not ir_yield2.is_yield_from()


class TestAsyncIRLimitations:
    """Test current limitations and expected failures."""
    
    def test_unsupported_async_constructs(self):
        """Document async constructs that are not yet supported."""
        # These are constructs that may need additional IR support
        
        # async with statement
        async_with_code = "async with lock: pass"
        async_with_ast = ast.parse(async_with_code).body[0]
        assert isinstance(async_with_ast, ast.AsyncWith)
        # Currently no IRAsyncWith - would need to be added
        
        # async for statement  
        async_for_code = "async for item in async_iter(): pass"
        async_for_ast = ast.parse(async_for_code).body[0]
        assert isinstance(async_for_ast, ast.AsyncFor)
        # Currently no IRAsyncFor - would need to be added
        
        # These limitations are documented for future implementation
        pass
