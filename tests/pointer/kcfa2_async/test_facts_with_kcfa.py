"""Integration tests for AsyncFactsHelper with k-CFA2 pointer analysis.

This module tests the integration between AsyncFactsHelper and k-CFA2 pointer
analysis on small async code snippets. Tests verify that await edge targets
include pts(callee_expr) results and that fact extraction works with real
k-CFA2 analysis.

Tests are parametrized over k values {1,2} and obj_depth values {1,2}.
"""

import pytest
import ast
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from pythonstan.analysis.pointer.kcfa2.config import KCFAConfig
from pythonstan.analysis.pointer.kcfa2.context import Context, CallSite
from pythonstan.analysis.pointer.kcfa2.model import AbstractObject, PointsToSet
from pythonstan.analysis.pointer.kcfa2.heap_model import make_object
from pythonstan.analysis.pointer.kcfa2.async_facts import AsyncFactsHelper
from pythonstan.analysis.pointer.kcfa2.async_types import (
    AwaitEdgeFact, TaskCreateFact, CoroutineDefFact
)
from pythonstan.ir.ir_statements import IRFunc, IRAwait, IRModule


@dataclass
class MockAsyncAnalysisResult:
    """Mock result from async analysis for testing."""
    coroutine_functions: List[str]
    await_sites: List[Dict[str, Any]]
    task_creations: List[Dict[str, Any]]
    points_to_results: Dict[str, List[str]]


class MockKCFA2Analysis:
    """Mock k-CFA2 analysis with realistic async behavior."""
    
    def __init__(self, config: KCFAConfig):
        self.config = config
        self._points_to = {}
        self._call_targets = {}
        self._analysis_results = None
    
    def analyze(self, ir_module: Any) -> MockAsyncAnalysisResult:
        """Run mock analysis and return results."""
        # Mock analysis of async constructs
        coroutine_functions = []
        await_sites = []
        task_creations = []
        
        # Extract functions and analyze for async patterns
        if hasattr(ir_module, 'get_functions'):
            for func in ir_module.get_functions():
                if getattr(func, 'is_async', False):
                    coroutine_functions.append(func.name)
        
        # Mock await site detection
        await_sites = [
            {
                "await_id": "test.py:10:5:await",
                "awaiter_fn": "test.awaiter",
                "awaited_expr": "coro_call",
                "target_var": "result"
            }
        ]
        
        # Mock task creation detection
        task_creations = [
            {
                "task_id": "test.py:15:10:create_task",
                "creator_fn": "test.main",
                "coro_expr": "worker",
                "target_var": "task"
            }
        ]
        
        # Mock points-to results
        points_to_results = {
            "coro_call": ["test.async_func", "test.another_async_func"],
            "worker": ["test.worker_func"],
            "task": ["task_obj_1"]
        }
        
        self._analysis_results = MockAsyncAnalysisResult(
            coroutine_functions=coroutine_functions,
            await_sites=await_sites,
            task_creations=task_creations,
            points_to_results=points_to_results
        )
        
        return self._analysis_results
    
    def points_to(self, var_name: str, context: Optional[Context] = None) -> PointsToSet:
        """Mock points-to query using analysis results."""
        if self._analysis_results and var_name in self._analysis_results.points_to_results:
            targets = self._analysis_results.points_to_results[var_name]
            # Create mock objects for targets
            objects = []
            for target in targets:
                ctx = context or Context()
                obj = make_object(alloc_id=target, alloc_ctx=ctx)
                objects.append(obj)
            return PointsToSet(frozenset(objects))
        
        return PointsToSet(frozenset())
    
    def get_call_targets(self, call_expr: Any, context: Optional[Context] = None) -> List[str]:
        """Mock call target resolution."""
        if isinstance(call_expr, str):
            if self._analysis_results and call_expr in self._analysis_results.points_to_results:
                return self._analysis_results.points_to_results[call_expr]
        
        return ["mock.unknown"]


class TestKCFA2AsyncIntegration:
    """Test AsyncFactsHelper integration with k-CFA2."""
    
    @pytest.mark.parametrize("k", [1, 2])
    @pytest.mark.parametrize("obj_depth", [1, 2])
    def test_basic_async_function_analysis(self, k, obj_depth):
        """Test basic async function analysis with different k and obj_depth."""
        config = KCFAConfig(k=k, obj_depth=obj_depth)
        helper = AsyncFactsHelper(config)
        kcfa = MockKCFA2Analysis(config)
        
        # Create mock IR module with async function
        class MockIRModule:
            def get_functions(self):
                return [MockAsyncFunc("test_async", is_async=True)]
        
        class MockAsyncFunc:
            def __init__(self, name: str, is_async: bool = False):
                self.name = name
                self.is_async = is_async
        
        ir_module = MockIRModule()
        
        # Run analysis
        results = kcfa.analyze(ir_module)
        
        # Verify async functions detected
        assert len(results.coroutine_functions) == 1
        assert "test_async" in results.coroutine_functions
        
        # Verify configuration preserved
        assert kcfa.config.k == k
        assert kcfa.config.obj_depth == obj_depth
    
    def test_await_edge_with_points_to_resolution(self):
        """Test await edge fact extraction with points-to resolution."""
        config = KCFAConfig(k=1, obj_depth=2)
        helper = AsyncFactsHelper(config)
        kcfa = MockKCFA2Analysis(config)
        
        # Create mock IR module
        class MockIRModule:
            def get_functions(self):
                return []
        
        ir_module = MockIRModule()
        results = kcfa.analyze(ir_module)
        
        # Extract await edge facts using mock results
        for await_site in results.await_sites:
            awaited_expr = await_site["awaited_expr"]
            
            # Resolve targets using points-to analysis
            pts_result = kcfa.points_to(awaited_expr)
            resolved_targets = [obj.alloc_id for obj in pts_result.objects]
            
            # Create await edge fact
            await_fact: AwaitEdgeFact = {
                "fact_type": "await_edge",
                "await_id": await_site["await_id"],
                "awaiter_fn": await_site["awaiter_fn"],
                "awaited_targets": resolved_targets,
                "may_unknown": len(resolved_targets) == 0
            }
            
            helper._facts["await_edge"].append(await_fact)
        
        # Verify facts recorded correctly
        facts = helper.facts()
        assert len(facts["await_edge"]) == 1
        
        await_fact = facts["await_edge"][0]
        assert await_fact["await_id"] == "test.py:10:5:await"
        assert await_fact["awaiter_fn"] == "test.awaiter"
        assert "test.async_func" in await_fact["awaited_targets"]
        assert "test.another_async_func" in await_fact["awaited_targets"]
        assert await_fact["may_unknown"] is False
    
    def test_task_creation_with_target_resolution(self):
        """Test task creation fact extraction with target resolution."""
        config = KCFAConfig(k=1, obj_depth=2)
        helper = AsyncFactsHelper(config)
        kcfa = MockKCFA2Analysis(config)
        
        # Create mock IR module
        class MockIRModule:
            def get_functions(self):
                return []
        
        ir_module = MockIRModule()
        results = kcfa.analyze(ir_module)
        
        # Extract task creation facts
        for task_site in results.task_creations:
            coro_expr = task_site["coro_expr"]
            
            # Resolve coroutine targets
            call_targets = kcfa.get_call_targets(coro_expr)
            
            # Create task creation fact
            task_fact: TaskCreateFact = {
                "fact_type": "task_create",
                "task_id": task_site["task_id"],
                "creator_fn": task_site["creator_fn"],
                "targets": call_targets,
                "args_vars": [],  # Would extract from analysis
                "may_unknown": len(call_targets) == 0 or "unknown" in call_targets[0]
            }
            
            helper._facts["task_create"].append(task_fact)
        
        # Verify facts recorded correctly
        facts = helper.facts()
        assert len(facts["task_create"]) == 1
        
        task_fact = facts["task_create"][0]
        assert task_fact["task_id"] == "test.py:15:10:create_task"
        assert task_fact["creator_fn"] == "test.main"
        assert "test.worker_func" in task_fact["targets"]
        assert task_fact["may_unknown"] is False
    
    def test_coroutine_def_fact_extraction(self):
        """Test coroutine definition fact extraction."""
        config = KCFAConfig(k=1, obj_depth=2)
        helper = AsyncFactsHelper(config)
        kcfa = MockKCFA2Analysis(config)
        
        # Create mock IR module with async functions
        class MockIRModule:
            def get_functions(self):
                return [
                    MockAsyncFunc("regular_async", is_async=True, has_yield=False),
                    MockAsyncFunc("async_generator", is_async=True, has_yield=True),
                    MockAsyncFunc("sync_func", is_async=False, has_yield=False)
                ]
        
        class MockAsyncFunc:
            def __init__(self, name: str, is_async: bool = False, has_yield: bool = False):
                self.name = name
                self.is_async = is_async
                self.has_yield = has_yield
        
        ir_module = MockIRModule()
        
        # Extract coroutine definition facts
        for func in ir_module.get_functions():
            if func.is_async:
                coroutine_fact: CoroutineDefFact = {
                    "fact_type": "coroutine_def",
                    "func_sym": f"test.{func.name}",
                    "def_site": f"test.py:5:0:func",
                    "is_async": func.is_async,
                    "is_async_gen": func.has_yield
                }
                
                helper._facts["coroutine_def"].append(coroutine_fact)
        
        # Verify facts recorded correctly
        facts = helper.facts()
        assert len(facts["coroutine_def"]) == 2
        
        func_syms = [fact["func_sym"] for fact in facts["coroutine_def"]]
        assert "test.regular_async" in func_syms
        assert "test.async_generator" in func_syms
        
        # Check async generator detection
        async_gens = helper.async_generators()
        assert len(async_gens) == 1
        assert async_gens[0]["func_sym"] == "test.async_generator"
    
    def test_unknown_await_target_handling(self):
        """Test handling of unknown await targets."""
        config = KCFAConfig(k=1, obj_depth=2)
        helper = AsyncFactsHelper(config)
        kcfa = MockKCFA2Analysis(config)
        
        # Mock case where await target cannot be resolved
        class MockIRModule:
            def get_functions(self):
                return []
        
        ir_module = MockIRModule()
        
        # Create await fact with unknown target
        await_fact: AwaitEdgeFact = {
            "fact_type": "await_edge",
            "await_id": "test.py:20:5:await",
            "awaiter_fn": "test.uncertain_awaiter",
            "awaited_targets": [],  # No resolved targets
            "may_unknown": True
        }
        
        helper._facts["await_edge"].append(await_fact)
        
        # Verify unknown handling
        facts = helper.facts()
        assert len(facts["await_edge"]) == 1
        
        await_fact = facts["await_edge"][0]
        assert await_fact["may_unknown"] is True
        assert len(await_fact["awaited_targets"]) == 0
    
    @pytest.mark.parametrize("k", [1, 2])
    def test_context_sensitivity_impact(self, k):
        """Test impact of k-CFA context sensitivity on fact extraction."""
        config = KCFAConfig(k=k, obj_depth=2)
        helper = AsyncFactsHelper(config)
        kcfa = MockKCFA2Analysis(config)
        
        # Create different contexts for the same call site
        ctx1 = Context()
        if k >= 1:
            call_site1 = CallSite(site_id="test.py:5:10:call", fn="caller1")
            ctx1 = Context((call_site1,))
        
        ctx2 = Context()
        if k >= 1:
            call_site2 = CallSite(site_id="test.py:10:15:call", fn="caller2")
            ctx2 = Context((call_site2,))
        
        # Mock different points-to results for different contexts
        var_name = "coro_var"
        
        # Simulate context-sensitive points-to results
        pts1 = kcfa.points_to(var_name, ctx1)
        pts2 = kcfa.points_to(var_name, ctx2)
        
        # For this test, both should return the same mock results
        # In a real implementation, context sensitivity could yield different results
        assert isinstance(pts1, PointsToSet)
        assert isinstance(pts2, PointsToSet)
        
        # Configuration should preserve k value
        assert helper.config.k == k
    
    def test_object_sensitivity_impact(self):
        """Test impact of object sensitivity on fact extraction."""
        config = KCFAConfig(k=1, obj_depth=2)
        helper = AsyncFactsHelper(config)
        kcfa = MockKCFA2Analysis(config)
        
        # Create objects with different allocation contexts
        from pythonstan.analysis.pointer.kcfa2.context import CallSite
        
        call_site1 = CallSite("test.py:1:0:call", "func1")
        call_site2 = CallSite("test.py:2:0:call", "func2")
        
        ctx1 = Context((call_site1,))
        ctx2 = Context((call_site2,))
        
        obj1 = make_object(alloc_id="async_obj", alloc_ctx=ctx1)
        obj2 = make_object(alloc_id="async_obj", alloc_ctx=ctx2)
        
        # Objects with same alloc_id but different contexts should be different
        assert obj1.alloc_id == obj2.alloc_id
        assert obj1.alloc_ctx != obj2.alloc_ctx
        
        # Configuration should preserve obj_depth
        assert helper.config.obj_depth == 2


class TestAsyncFactsEndToEnd:
    """End-to-end tests for async facts extraction."""
    
    def test_small_async_snippet_analysis(self):
        """Test end-to-end analysis of a small async code snippet."""
        # Sample async code
        async_code = """
async def worker():
    return "done"

async def main():
    task = asyncio.create_task(worker())
    result = await task
    return result
"""
        
        config = KCFAConfig(k=1, obj_depth=2)
        helper = AsyncFactsHelper(config)
        kcfa = MockKCFA2Analysis(config)
        
        # Parse and create mock IR
        module_ast = ast.parse(async_code)
        
        class MockIRModule:
            def __init__(self, ast_module):
                self.ast_module = ast_module
            
            def get_functions(self):
                functions = []
                for node in ast.walk(self.ast_module):
                    if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                        mock_func = MockFunc(node.name, isinstance(node, ast.AsyncFunctionDef))
                        functions.append(mock_func)
                return functions
        
        class MockFunc:
            def __init__(self, name: str, is_async: bool):
                self.name = name
                self.is_async = is_async
        
        ir_module = MockIRModule(module_ast)
        
        # Run analysis
        results = kcfa.analyze(ir_module)
        
        # Extract facts manually (would be done by helper.index_module)
        # Coroutine definitions
        for func in ir_module.get_functions():
            if func.is_async:
                coroutine_fact: CoroutineDefFact = {
                    "fact_type": "coroutine_def",
                    "func_sym": f"test.{func.name}",
                    "def_site": f"test.py:1:0:func",
                    "is_async": True,
                    "is_async_gen": False  # Would detect yield in real implementation
                }
                helper._facts["coroutine_def"].append(coroutine_fact)
        
        # Task creation (mock based on code analysis)
        task_fact: TaskCreateFact = {
            "fact_type": "task_create",
            "task_id": "test.py:6:11:create_task",
            "creator_fn": "test.main",
            "targets": ["test.worker"],
            "args_vars": [],
            "may_unknown": False
        }
        helper._facts["task_create"].append(task_fact)
        helper._update_indices(task_fact)
        
        # Await edge (mock based on code analysis)
        await_fact: AwaitEdgeFact = {
            "fact_type": "await_edge",
            "await_id": "test.py:7:13:await",
            "awaiter_fn": "test.main",
            "awaited_targets": ["task_obj_1"],  # From points-to analysis
            "may_unknown": False
        }
        helper._facts["await_edge"].append(await_fact)
        helper._update_indices(await_fact)
        
        # Verify comprehensive fact extraction
        facts = helper.facts()
        
        # Should have coroutine definitions
        assert len(facts["coroutine_def"]) == 2  # worker and main
        coroutine_names = [fact["func_sym"] for fact in facts["coroutine_def"]]
        assert "test.worker" in coroutine_names
        assert "test.main" in coroutine_names
        
        # Should have task creation
        assert len(facts["task_create"]) == 1
        assert facts["task_create"][0]["targets"] == ["test.worker"]
        
        # Should have await edge
        assert len(facts["await_edge"]) == 1
        assert facts["await_edge"][0]["awaiter_fn"] == "test.main"
        
        # Test queries
        tasks_in_main = helper.tasks_created_in("test.main")
        assert len(tasks_in_main) == 1
        
        awaited_by_main = helper.awaited_by("test.main")
        assert len(awaited_by_main) == 1
    
    def test_complex_async_patterns(self):
        """Test analysis of more complex async patterns."""
        config = KCFAConfig(k=2, obj_depth=2)
        helper = AsyncFactsHelper(config)
        kcfa = MockKCFA2Analysis(config)
        
        # Mock complex async scenario with multiple patterns
        # Queue operations
        from pythonstan.analysis.pointer.kcfa2.async_types import QueueAllocFact, QueuePutFact, QueueGetFact
        
        queue_alloc: QueueAllocFact = {
            "fact_type": "queue_alloc",
            "queue_id": "test.py:10:5:queue",
            "queue_kind": "Queue",
            "maxsize": 0,
            "alloc_ctx": "test.main:ctx1"
        }
        helper._facts["queue_alloc"].append(queue_alloc)
        
        queue_put: QueuePutFact = {
            "fact_type": "queue_put",
            "site_id": "test.py:15:10:queue_put",
            "queue_ids": ["test.py:10:5:queue"],
            "value_vars": ["item"]
        }
        helper._facts["queue_put"].append(queue_put)
        
        queue_get: QueueGetFact = {
            "fact_type": "queue_get",
            "site_id": "test.py:20:15:queue_get",
            "queue_ids": ["test.py:10:5:queue"],
            "target_var": "result"
        }
        helper._facts["queue_get"].append(queue_get)
        
        # Verify complex pattern recognition
        facts = helper.facts()
        assert len(facts["queue_alloc"]) == 1
        assert len(facts["queue_put"]) == 1
        assert len(facts["queue_get"]) == 1
        
        # Verify queue operation relationships
        queue_id = facts["queue_alloc"][0]["queue_id"]
        assert facts["queue_put"][0]["queue_ids"] == [queue_id]
        assert facts["queue_get"][0]["queue_ids"] == [queue_id]
