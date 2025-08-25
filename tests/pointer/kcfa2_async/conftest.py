"""Fixtures for k-CFA2 async pointer analysis tests.

This module provides pytest fixtures for testing the async extensions to 
k-CFA2 pointer analysis implementation in PythonStAn.
"""

import pytest
import ast
from typing import List, Dict, Any, Optional
from pathlib import Path

from pythonstan.analysis.pointer.kcfa2.config import KCFAConfig
from pythonstan.analysis.pointer.kcfa2.context import CallSite, Context
from pythonstan.analysis.pointer.kcfa2.model import AbstractObject, PointsToSet
from pythonstan.analysis.pointer.kcfa2.heap_model import make_object
from pythonstan.analysis.pointer.kcfa2.async_facts import AsyncFactsHelper
from pythonstan.ir.ir_statements import IRModule, IRFunc, IRAwait, IRYield


@pytest.fixture(params=[1, 2])
def k(request):
    """Fixture for k values in k-CFA."""
    return request.param


@pytest.fixture(params=[1, 2])
def obj_depth(request):
    """Fixture for object sensitivity depth."""
    return request.param


@pytest.fixture
def async_config(k, obj_depth):
    """Fixture for k-CFA configuration with async support."""
    return KCFAConfig(k=k, obj_depth=obj_depth)


@pytest.fixture
def async_facts_helper(async_config):
    """Fixture for AsyncFactsHelper instance."""
    return AsyncFactsHelper(async_config)


@pytest.fixture
def transform_entrypoints():
    """Load transform entrypoints configuration."""
    entrypoints_path = Path(__file__).parent.parent.parent.parent / "docs/digests/transform-entrypoints.json"
    
    if entrypoints_path.exists():
        import json
        with entrypoints_path.open() as f:
            return json.load(f)
    
    # Fallback mock configuration if file doesn't exist
    return {
        "ir": [
            {"symbol": "IR", "path": "pythonstan/analysis/transform/ir.py", "signature": "class IR(Transform)", "confidence": 0.9}
        ],
        "tac": [
            {"symbol": "ThreeAddress", "path": "pythonstan/analysis/transform/three_address.py", "signature": "class ThreeAddress(Transform)", "confidence": 0.9}
        ],
        "cfg": [
            {"symbol": "BlockCFG", "path": "pythonstan/analysis/transform/block_cfg.py", "signature": "class BlockCFG(Transform)", "confidence": 0.9},
            {"symbol": "CFG", "path": "pythonstan/analysis/transform/cfg.py", "signature": "class CFG(Transform)", "confidence": 0.8}
        ]
    }


@pytest.fixture
def mock_kcfa_analysis():
    """Mock k-CFA2 pointer analysis for testing async facts extraction."""
    class MockKCFA2Analysis:
        def __init__(self):
            self.config = KCFAConfig(k=1, obj_depth=2)
            self._points_to = {}
            
        def points_to(self, var_name: str, context: Optional[Context] = None) -> PointsToSet:
            """Mock points-to query."""
            key = (var_name, context)
            return self._points_to.get(key, PointsToSet(frozenset()))
            
        def set_points_to(self, var_name: str, objects: List[AbstractObject], 
                         context: Optional[Context] = None):
            """Set mock points-to results for testing."""
            key = (var_name, context)
            self._points_to[key] = PointsToSet(frozenset(objects))
            
        def get_call_targets(self, call_expr: Any, context: Optional[Context] = None) -> List[str]:
            """Mock call target resolution."""
            # Return mock targets for testing
            if hasattr(call_expr, 'id'):
                return [f"mock.{call_expr.id}"]
            return ["mock.unknown_function"]
    
    return MockKCFA2Analysis()


@pytest.fixture 
def sample_async_code():
    """Sample async Python code snippets for testing."""
    return {
        "simple_async": """
async def simple_coro():
    return 42

async def awaiter():
    result = await simple_coro()
    return result
""",
        "async_generator": """
async def async_gen():
    yield 1
    yield 2
    yield 3
""",
        "task_creation": """
import asyncio

async def worker():
    return "done"

async def main():
    task = asyncio.create_task(worker())
    result = await task
    return result
""",
        "queue_operations": """
import asyncio

async def producer(queue):
    await queue.put("item")

async def consumer(queue):
    item = await queue.get()
    return item

async def main():
    q = asyncio.Queue()
    await producer(q)
    result = await consumer(q)
    return result
""",
        "sync_primitives": """
import asyncio

async def worker(lock):
    async with lock:
        return "critical section"

async def main():
    lock = asyncio.Lock()
    result = await worker(lock)
    return result
"""
    }


@pytest.fixture
def parse_async_code(sample_async_code):
    """Fixture to parse async code samples into AST."""
    def _parse(code_key: str) -> ast.Module:
        code = sample_async_code[code_key]
        return ast.parse(code)
    return _parse


@pytest.fixture  
def mock_ir_module():
    """Mock IR module with async functions for testing."""
    class MockIRModule:
        def __init__(self, name: str = "test_module"):
            self.name = name
            self.functions = []
            
        def add_function(self, func: IRFunc):
            self.functions.append(func)
            
        def get_functions(self) -> List[IRFunc]:
            return self.functions
            
        def __iter__(self):
            return iter(self.functions)
    
    return MockIRModule()


@pytest.fixture
def mock_async_ir_events():
    """Mock async IR events for testing event mapping."""
    return {
        "await_event": {
            "kind": "await",
            "await_id": "test.py:10:5:await",
            "awaiter_fn": "test.awaiter",
            "awaited_expr": ast.Name(id="coro_call", ctx=ast.Load()),
            "target_var": "result"
        },
        "task_create_event": {
            "kind": "task_create", 
            "task_id": "test.py:15:10:create_task",
            "creator_fn": "test.main",
            "coro_expr": ast.Call(
                func=ast.Name(id="worker", ctx=ast.Load()),
                args=[],
                keywords=[]
            ),
            "target_var": "task"
        },
        "queue_alloc_event": {
            "kind": "queue_alloc",
            "queue_id": "test.py:20:5:queue",
            "queue_kind": "Queue", 
            "maxsize": 0,
            "target_var": "q"
        }
    }
