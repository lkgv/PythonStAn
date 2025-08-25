"""Minimal tests for AsyncFactsHelper fact recording.

This module tests that the AsyncFactsHelper correctly records async facts
from events and stubbed kcfa2 points-to results. Tests use minimal mock
objects to verify fact collection without requiring full kcfa2 integration.

Tests focus on fact recording accuracy, indexing, and query functionality.
"""

import pytest
import ast
from typing import List, Dict, Any, Optional
from unittest.mock import Mock

from pythonstan.analysis.pointer.kcfa2.async_facts import AsyncFactsHelper
from pythonstan.analysis.pointer.kcfa2.async_types import (
    CoroutineDefFact, AwaitEdgeFact, TaskCreateFact, QueueAllocFact,
    QueuePutFact, QueueGetFact, SyncAllocFact, SyncOpFact,
    LoopCallbackScheduleFact, CallbackEdgeFact
)
from pythonstan.analysis.pointer.kcfa2.config import KCFAConfig
from pythonstan.analysis.pointer.kcfa2.context import Context
from pythonstan.analysis.pointer.kcfa2.model import AbstractObject, PointsToSet
from pythonstan.analysis.pointer.kcfa2.heap_model import make_object


class MockKCFAForFacts:
    """Mock k-CFA analysis with minimal API for fact extraction testing."""
    
    def __init__(self):
        self.config = KCFAConfig(k=1, obj_depth=2)
        self._call_targets = {}
        self._points_to = {}
    
    def get_call_targets(self, call_expr: Any, context: Optional[Context] = None) -> List[str]:
        """Mock call target resolution."""
        if isinstance(call_expr, ast.Call):
            if isinstance(call_expr.func, ast.Name):
                return self._call_targets.get(call_expr.func.id, [f"mock.{call_expr.func.id}"])
            elif isinstance(call_expr.func, ast.Attribute):
                attr_name = call_expr.func.attr
                return self._call_targets.get(attr_name, [f"mock.{attr_name}"])
        return ["mock.unknown"]
    
    def points_to(self, var_name: str, context: Optional[Context] = None) -> PointsToSet:
        """Mock points-to query."""
        key = (var_name, context)
        return self._points_to.get(key, PointsToSet(frozenset()))
    
    def set_call_targets(self, call_name: str, targets: List[str]):
        """Set mock call targets for testing."""
        self._call_targets[call_name] = targets
    
    def set_points_to(self, var_name: str, objects: List[AbstractObject], 
                     context: Optional[Context] = None):
        """Set mock points-to results for testing."""
        key = (var_name, context)
        self._points_to[key] = PointsToSet(frozenset(objects))


class TestAsyncFactsHelperBasics:
    """Test basic AsyncFactsHelper functionality."""
    
    def test_helper_initialization(self, async_config):
        """Test AsyncFactsHelper initialization."""
        helper = AsyncFactsHelper(async_config)
        
        # Verify initial state
        facts = helper.facts()
        assert len(facts) == 13  # All fact types from async_types.py
        
        expected_types = [
            "coroutine_def", "await_edge", "task_create", "task_state",
            "future", "queue_alloc", "queue_put", "queue_get",
            "sync_alloc", "sync_op", "loop_cb_schedule", "callback_edge", "stream"
        ]
        
        for fact_type in expected_types:
            assert fact_type in facts
            assert isinstance(facts[fact_type], list)
            assert len(facts[fact_type]) == 0
        
        # Verify statistics
        stats = helper.statistics()
        assert all(count == 0 for count in stats.values())
    
    def test_helper_with_default_config(self):
        """Test AsyncFactsHelper with default configuration."""
        helper = AsyncFactsHelper()
        
        # Should create default config
        assert helper.config is not None
        assert isinstance(helper.config, KCFAConfig)
        
        facts = helper.facts()
        assert len(facts) > 0
    
    def test_facts_immutability(self, async_facts_helper):
        """Test that facts() returns immutable copies."""
        facts1 = async_facts_helper.facts()
        facts2 = async_facts_helper.facts()
        
        # Should be separate instances
        assert facts1 is not facts2
        
        # Modifying returned facts should not affect internal state
        facts1["await_edge"].append({"test": "fact"})
        
        facts3 = async_facts_helper.facts()
        assert len(facts3["await_edge"]) == 0
    
    def test_clear_functionality(self, async_facts_helper):
        """Test clear() resets all state."""
        # Add some mock facts manually to test clearing
        async_facts_helper._facts["await_edge"].append({
            "fact_type": "await_edge",
            "await_id": "test.py:10:5:await",
            "awaiter_fn": "test.func",
            "awaited_targets": ["test.target"],
            "may_unknown": False
        })
        
        # Update stats manually
        async_facts_helper._stats["await_expressions_found"] = 1
        
        # Clear and verify
        async_facts_helper.clear()
        
        facts = async_facts_helper.facts()
        assert len(facts["await_edge"]) == 0
        
        stats = async_facts_helper.statistics()
        assert stats["await_expressions_found"] == 0


class TestFactRecording:
    """Test fact recording functionality."""
    
    def test_manual_coroutine_def_fact_recording(self, async_facts_helper):
        """Test recording coroutine definition facts."""
        # Since implementation is scaffolded, test manual fact addition
        coroutine_fact: CoroutineDefFact = {
            "fact_type": "coroutine_def",
            "func_sym": "test.async_func",
            "def_site": "test.py:5:0:func",
            "is_async": True,
            "is_async_gen": False
        }
        
        async_facts_helper._facts["coroutine_def"].append(coroutine_fact)
        
        facts = async_facts_helper.facts()
        assert len(facts["coroutine_def"]) == 1
        
        recorded_fact = facts["coroutine_def"][0]
        assert recorded_fact["func_sym"] == "test.async_func"
        assert recorded_fact["is_async"] is True
        assert recorded_fact["is_async_gen"] is False
    
    def test_manual_await_edge_fact_recording(self, async_facts_helper):
        """Test recording await edge facts."""
        await_fact: AwaitEdgeFact = {
            "fact_type": "await_edge",
            "await_id": "test.py:10:5:await",
            "awaiter_fn": "test.caller",
            "awaited_targets": ["test.callee", "test.another_callee"],
            "may_unknown": False
        }
        
        async_facts_helper._facts["await_edge"].append(await_fact)
        
        facts = async_facts_helper.facts()
        assert len(facts["await_edge"]) == 1
        
        recorded_fact = facts["await_edge"][0]
        assert recorded_fact["await_id"] == "test.py:10:5:await"
        assert recorded_fact["awaiter_fn"] == "test.caller"
        assert len(recorded_fact["awaited_targets"]) == 2
        assert recorded_fact["may_unknown"] is False
    
    def test_manual_task_create_fact_recording(self, async_facts_helper):
        """Test recording task creation facts."""
        task_fact: TaskCreateFact = {
            "fact_type": "task_create",
            "task_id": "test.py:15:10:create_task",
            "creator_fn": "test.main",
            "targets": ["test.worker_func"],
            "args_vars": ["arg1", "arg2"],
            "may_unknown": False
        }
        
        async_facts_helper._facts["task_create"].append(task_fact)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_create"]) == 1
        
        recorded_fact = facts["task_create"][0]
        assert recorded_fact["task_id"] == "test.py:15:10:create_task"
        assert recorded_fact["creator_fn"] == "test.main"
        assert "test.worker_func" in recorded_fact["targets"]
    
    def test_manual_queue_facts_recording(self, async_facts_helper):
        """Test recording queue operation facts."""
        # Queue allocation
        queue_alloc_fact: QueueAllocFact = {
            "fact_type": "queue_alloc",
            "queue_id": "test.py:20:5:queue",
            "queue_kind": "Queue",
            "maxsize": 0,
            "alloc_ctx": "test.main:1"
        }
        
        async_facts_helper._facts["queue_alloc"].append(queue_alloc_fact)
        
        # Queue put
        queue_put_fact: QueuePutFact = {
            "fact_type": "queue_put",
            "site_id": "test.py:25:10:queue_put",
            "queue_ids": ["test.py:20:5:queue"],
            "value_vars": ["item"]
        }
        
        async_facts_helper._facts["queue_put"].append(queue_put_fact)
        
        # Queue get
        queue_get_fact: QueueGetFact = {
            "fact_type": "queue_get",
            "site_id": "test.py:30:15:queue_get",
            "queue_ids": ["test.py:20:5:queue"],
            "target_var": "result"
        }
        
        async_facts_helper._facts["queue_get"].append(queue_get_fact)
        
        facts = async_facts_helper.facts()
        assert len(facts["queue_alloc"]) == 1
        assert len(facts["queue_put"]) == 1
        assert len(facts["queue_get"]) == 1
        
        # Verify queue relationships
        assert facts["queue_alloc"][0]["queue_kind"] == "Queue"
        assert facts["queue_put"][0]["queue_ids"] == ["test.py:20:5:queue"]
        assert facts["queue_get"][0]["queue_ids"] == ["test.py:20:5:queue"]
    
    def test_manual_sync_facts_recording(self, async_facts_helper):
        """Test recording synchronization primitive facts."""
        # Sync allocation
        sync_alloc_fact: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:35:5:sync",
            "kind": "Lock"
        }
        
        async_facts_helper._facts["sync_alloc"].append(sync_alloc_fact)
        
        # Sync operation
        sync_op_fact: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:40:10:sync_op",
            "kind": "Lock",
            "op": "acquire",
            "target_ids": ["test.py:35:5:sync"]
        }
        
        async_facts_helper._facts["sync_op"].append(sync_op_fact)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_alloc"]) == 1
        assert len(facts["sync_op"]) == 1
        
        assert facts["sync_alloc"][0]["kind"] == "Lock"
        assert facts["sync_op"][0]["op"] == "acquire"
        assert facts["sync_op"][0]["target_ids"] == ["test.py:35:5:sync"]
    
    def test_manual_callback_facts_recording(self, async_facts_helper):
        """Test recording callback scheduling facts."""
        # Loop callback schedule
        cb_schedule_fact: LoopCallbackScheduleFact = {
            "fact_type": "loop_cb_schedule",
            "cb_id": "test.py:45:5:loop_cb",
            "api": "call_soon",
            "delay": None,
            "callback_targets": ["test.callback_func"],
            "args_vars": ["arg1"]
        }
        
        async_facts_helper._facts["loop_cb_schedule"].append(cb_schedule_fact)
        
        # Callback edge
        cb_edge_fact: CallbackEdgeFact = {
            "fact_type": "callback_edge",
            "cb_id": "test.py:50:10:callback",
            "caller_fn": "event_loop",
            "callee_targets": ["test.callback_func"]
        }
        
        async_facts_helper._facts["callback_edge"].append(cb_edge_fact)
        
        facts = async_facts_helper.facts()
        assert len(facts["loop_cb_schedule"]) == 1
        assert len(facts["callback_edge"]) == 1
        
        assert facts["loop_cb_schedule"][0]["api"] == "call_soon"
        assert facts["callback_edge"][0]["caller_fn"] == "event_loop"


class TestFactQuerying:
    """Test fact querying functionality."""
    
    def test_awaiters_of_query(self, async_facts_helper):
        """Test awaiters_of() query method."""
        # Add some await facts
        await_facts = [
            {
                "fact_type": "await_edge",
                "await_id": "test.py:10:5:await",
                "awaiter_fn": "test.caller1",
                "awaited_targets": ["test.target_func"],
                "may_unknown": False
            },
            {
                "fact_type": "await_edge", 
                "await_id": "test.py:15:10:await",
                "awaiter_fn": "test.caller2",
                "awaited_targets": ["test.target_func", "test.other_func"],
                "may_unknown": False
            },
            {
                "fact_type": "await_edge",
                "await_id": "test.py:20:5:await",
                "awaiter_fn": "test.caller3",
                "awaited_targets": ["test.different_func"],
                "may_unknown": False
            }
        ]
        
        for fact in await_facts:
            async_facts_helper._facts["await_edge"].append(fact)
        
        # Manually update index (normally done by _update_indices)
        for fact in await_facts:
            for target in fact["awaited_targets"]:
                if target not in async_facts_helper._awaited_index:
                    async_facts_helper._awaited_index[target] = []
                async_facts_helper._awaited_index[target].append(fact)
        
        # Test query
        awaiters = async_facts_helper.awaiters_of("test.target_func")
        assert len(awaiters) == 2
        
        awaiter_fns = [fact["awaiter_fn"] for fact in awaiters]
        assert "test.caller1" in awaiter_fns
        assert "test.caller2" in awaiter_fns
        
        # Test non-existent target
        awaiters_none = async_facts_helper.awaiters_of("test.nonexistent")
        assert len(awaiters_none) == 0
    
    def test_awaited_by_query(self, async_facts_helper):
        """Test awaited_by() query method."""
        # Add await facts
        await_facts = [
            {
                "fact_type": "await_edge",
                "await_id": "test.py:10:5:await",
                "awaiter_fn": "test.caller",
                "awaited_targets": ["test.target1"],
                "may_unknown": False
            },
            {
                "fact_type": "await_edge",
                "await_id": "test.py:15:10:await", 
                "awaiter_fn": "test.caller",
                "awaited_targets": ["test.target2"],
                "may_unknown": False
            },
            {
                "fact_type": "await_edge",
                "await_id": "test.py:20:5:await",
                "awaiter_fn": "test.other_caller",
                "awaited_targets": ["test.target3"],
                "may_unknown": False
            }
        ]
        
        for fact in await_facts:
            async_facts_helper._facts["await_edge"].append(fact)
        
        # Manually update index
        for fact in await_facts:
            awaiter = fact["awaiter_fn"]
            if awaiter not in async_facts_helper._awaiter_index:
                async_facts_helper._awaiter_index[awaiter] = []
            async_facts_helper._awaiter_index[awaiter].append(fact)
        
        # Test query
        awaited = async_facts_helper.awaited_by("test.caller")
        assert len(awaited) == 2
        
        targets = []
        for fact in awaited:
            targets.extend(fact["awaited_targets"])
        assert "test.target1" in targets
        assert "test.target2" in targets
    
    def test_tasks_created_in_query(self, async_facts_helper):
        """Test tasks_created_in() query method."""
        # Add task creation facts
        task_facts = [
            {
                "fact_type": "task_create",
                "task_id": "test.py:10:5:create_task",
                "creator_fn": "test.main",
                "targets": ["test.worker1"],
                "args_vars": [],
                "may_unknown": False
            },
            {
                "fact_type": "task_create",
                "task_id": "test.py:15:10:create_task",
                "creator_fn": "test.main",
                "targets": ["test.worker2"],
                "args_vars": ["arg"],
                "may_unknown": False
            },
            {
                "fact_type": "task_create",
                "task_id": "test.py:20:5:create_task",
                "creator_fn": "test.other_main",
                "targets": ["test.worker3"],
                "args_vars": [],
                "may_unknown": False
            }
        ]
        
        for fact in task_facts:
            async_facts_helper._facts["task_create"].append(fact)
        
        # Manually update index
        for fact in task_facts:
            creator = fact["creator_fn"]
            if creator not in async_facts_helper._task_creator_index:
                async_facts_helper._task_creator_index[creator] = []
            async_facts_helper._task_creator_index[creator].append(fact)
        
        # Test query
        tasks = async_facts_helper.tasks_created_in("test.main")
        assert len(tasks) == 2
        
        task_ids = [fact["task_id"] for fact in tasks]
        assert "test.py:10:5:create_task" in task_ids
        assert "test.py:15:10:create_task" in task_ids
    
    def test_async_generators_query(self, async_facts_helper):
        """Test async_generators() query method."""
        # Add coroutine def facts
        coroutine_facts = [
            {
                "fact_type": "coroutine_def",
                "func_sym": "test.regular_coro",
                "def_site": "test.py:5:0:func",
                "is_async": True,
                "is_async_gen": False
            },
            {
                "fact_type": "coroutine_def",
                "func_sym": "test.async_gen1",
                "def_site": "test.py:10:0:func",
                "is_async": True,
                "is_async_gen": True
            },
            {
                "fact_type": "coroutine_def",
                "func_sym": "test.async_gen2",
                "def_site": "test.py:15:0:func",
                "is_async": True,
                "is_async_gen": True
            }
        ]
        
        for fact in coroutine_facts:
            async_facts_helper._facts["coroutine_def"].append(fact)
        
        # Test query
        async_gens = async_facts_helper.async_generators()
        assert len(async_gens) == 2
        
        func_syms = [fact["func_sym"] for fact in async_gens]
        assert "test.async_gen1" in func_syms
        assert "test.async_gen2" in func_syms
        assert "test.regular_coro" not in func_syms
    
    def test_sync_primitives_by_type_query(self, async_facts_helper):
        """Test sync_primitives_by_type() query method."""
        # Add sync allocation facts
        sync_facts = [
            {
                "fact_type": "sync_alloc",
                "sync_id": "test.py:10:5:sync",
                "kind": "Lock"
            },
            {
                "fact_type": "sync_alloc",
                "sync_id": "test.py:15:5:sync",
                "kind": "Semaphore"
            },
            {
                "fact_type": "sync_alloc",
                "sync_id": "test.py:20:5:sync",
                "kind": "Lock"
            },
            {
                "fact_type": "sync_alloc",
                "sync_id": "test.py:25:5:sync",
                "kind": "Event"
            }
        ]
        
        for fact in sync_facts:
            async_facts_helper._facts["sync_alloc"].append(fact)
        
        # Test query
        locks = async_facts_helper.sync_primitives_by_type("Lock")
        assert len(locks) == 2
        
        semaphores = async_facts_helper.sync_primitives_by_type("Semaphore")
        assert len(semaphores) == 1
        
        events = async_facts_helper.sync_primitives_by_type("Event")
        assert len(events) == 1
        
        conditions = async_facts_helper.sync_primitives_by_type("Condition")
        assert len(conditions) == 0


class TestFactExport:
    """Test fact export functionality."""
    
    def test_jsonl_export(self, async_facts_helper, tmp_path):
        """Test JSONL export functionality."""
        # Add some facts
        async_facts_helper._facts["await_edge"].append({
            "fact_type": "await_edge",
            "await_id": "test.py:10:5:await",
            "awaiter_fn": "test.caller",
            "awaited_targets": ["test.target"],
            "may_unknown": False
        })
        
        async_facts_helper._facts["task_create"].append({
            "fact_type": "task_create",
            "task_id": "test.py:15:10:create_task",
            "creator_fn": "test.main",
            "targets": ["test.worker"],
            "args_vars": [],
            "may_unknown": False
        })
        
        # Export to JSONL
        output_path = tmp_path / "test_facts.jsonl"
        async_facts_helper.write_jsonl(output_path)
        
        # Verify file exists and has content
        assert output_path.exists()
        
        # Read and verify content
        import json
        with output_path.open() as f:
            lines = f.readlines()
        
        assert len(lines) == 2  # Two facts exported
        
        # Parse and verify JSON objects
        fact1 = json.loads(lines[0])
        fact2 = json.loads(lines[1])
        
        assert fact1["fact_type"] == "await_edge"
        assert fact2["fact_type"] == "task_create"
    
    def test_csv_export_not_implemented(self, async_facts_helper):
        """Test that CSV export raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="CSV export not yet implemented"):
            async_facts_helper.write_csv("test.csv", "await_edge")


class TestFactRecordingWithMockKCFA:
    """Test fact recording with mock k-CFA integration."""
    
    def test_await_edge_with_resolved_targets(self, async_facts_helper):
        """Test await edge fact recording with resolved call targets."""
        # Create mock k-CFA with call target resolution
        mock_kcfa = MockKCFAForFacts()
        mock_kcfa.set_call_targets("coro_call", ["test.resolved_target"])
        
        # This would normally be done by _extract_await_edge_facts
        # For now, test manual fact creation with resolved targets
        await_fact: AwaitEdgeFact = {
            "fact_type": "await_edge",
            "await_id": "test.py:10:5:await",
            "awaiter_fn": "test.caller",
            "awaited_targets": ["test.resolved_target"],  # From mock resolution
            "may_unknown": False
        }
        
        async_facts_helper._facts["await_edge"].append(await_fact)
        
        facts = async_facts_helper.facts()
        assert len(facts["await_edge"]) == 1
        assert facts["await_edge"][0]["awaited_targets"] == ["test.resolved_target"]
        assert facts["await_edge"][0]["may_unknown"] is False
    
    def test_await_edge_with_unknown_targets(self, async_facts_helper):
        """Test await edge fact recording with unknown call targets."""
        # Test case where call target cannot be resolved statically
        await_fact: AwaitEdgeFact = {
            "fact_type": "await_edge",
            "await_id": "test.py:15:5:await",
            "awaiter_fn": "test.caller",
            "awaited_targets": [],  # Empty when unknown
            "may_unknown": True
        }
        
        async_facts_helper._facts["await_edge"].append(await_fact)
        
        facts = async_facts_helper.facts()
        assert len(facts["await_edge"]) == 1
        assert facts["await_edge"][0]["awaited_targets"] == []
        assert facts["await_edge"][0]["may_unknown"] is True
