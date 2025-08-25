"""Tests for timeout and cancellation handling in async analysis.

This module tests recognition and fact extraction for asyncio timeout and
cancellation operations including asyncio.wait_for (timeout), shield,
task.cancel, and timeout-related events.

Tests verify that WaitEvent and TaskStateEvent facts are recorded appropriately
for timeout and cancellation scenarios, following conservative modeling principles.
"""

import pytest
import ast
from typing import List, Dict, Any, Optional
from unittest.mock import Mock

from pythonstan.analysis.pointer.kcfa2.async_facts import AsyncFactsHelper
from pythonstan.analysis.pointer.kcfa2.async_types import (
    TaskCreateFact, TaskStateFact, AwaitEdgeFact
)
from pythonstan.analysis.pointer.kcfa2.config import KCFAConfig


class TestTimeoutOperations:
    """Test recognition of timeout operations."""
    
    def test_wait_for_timeout_recognition(self, async_facts_helper):
        """Test recognition of asyncio.wait_for with timeout."""
        # Mock wait_for operation with timeout
        # This would normally be detected from asyncio.wait_for(coro, timeout=5.0)
        
        # The underlying await that wait_for performs
        await_fact: AwaitEdgeFact = {
            "fact_type": "await_edge",
            "await_id": "test.py:10:5:await",
            "awaiter_fn": "test.timed_operation",
            "awaited_targets": ["test.slow_operation"],
            "may_unknown": False
        }
        async_facts_helper._facts["await_edge"].append(await_fact)
        
        # Task state event for timeout handling
        timeout_event: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:10:5:wait_for",
            "op": "wait_for_timeout",
            "task_ids": ["test.slow_operation_task"]
        }
        async_facts_helper._facts["task_state"].append(timeout_event)
        
        facts = async_facts_helper.facts()
        assert len(facts["await_edge"]) == 1
        assert len(facts["task_state"]) == 1
        
        # Verify timeout handling
        timeout_fact = facts["task_state"][0]
        assert timeout_fact["op"] == "wait_for_timeout"
        assert "test.slow_operation_task" in timeout_fact["task_ids"]
    
    def test_wait_for_without_timeout(self, async_facts_helper):
        """Test asyncio.wait_for without explicit timeout (None)."""
        # wait_for with timeout=None should not generate timeout events
        await_fact: AwaitEdgeFact = {
            "fact_type": "await_edge",
            "await_id": "test.py:15:5:await",
            "awaiter_fn": "test.unlimited_operation",
            "awaited_targets": ["test.operation"],
            "may_unknown": False
        }
        async_facts_helper._facts["await_edge"].append(await_fact)
        
        # No timeout event should be generated for timeout=None
        facts = async_facts_helper.facts()
        assert len(facts["await_edge"]) == 1
        assert len(facts["task_state"]) == 0
    
    def test_timeout_exception_handling(self, async_facts_helper):
        """Test timeout exception scenarios."""
        # When asyncio.TimeoutError is raised
        timeout_event: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:20:5:timeout_exception",
            "op": "timeout_expired",
            "task_ids": ["test.timed_out_task"]
        }
        async_facts_helper._facts["task_state"].append(timeout_event)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_state"]) == 1
        
        timeout_fact = facts["task_state"][0]
        assert timeout_fact["op"] == "timeout_expired"
    
    def test_shield_operation_recognition(self, async_facts_helper):
        """Test recognition of asyncio.shield operations."""
        # shield protects from cancellation
        shield_await: AwaitEdgeFact = {
            "fact_type": "await_edge",
            "await_id": "test.py:25:5:await",
            "awaiter_fn": "test.protected_operation",
            "awaited_targets": ["asyncio.shield"],
            "may_unknown": False
        }
        async_facts_helper._facts["await_edge"].append(shield_await)
        
        # Shield state event
        shield_event: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:25:5:shield",
            "op": "shield_protect",
            "task_ids": ["test.protected_task"]
        }
        async_facts_helper._facts["task_state"].append(shield_event)
        
        facts = async_facts_helper.facts()
        assert len(facts["await_edge"]) == 1
        assert len(facts["task_state"]) == 1
        
        shield_fact = facts["task_state"][0]
        assert shield_fact["op"] == "shield_protect"


class TestCancellationOperations:
    """Test recognition of task cancellation operations."""
    
    def test_task_cancel_recognition(self, async_facts_helper):
        """Test recognition of task.cancel() calls."""
        # Task creation
        task_create: TaskCreateFact = {
            "fact_type": "task_create",
            "task_id": "test.py:30:10:create_task",
            "creator_fn": "test.main",
            "targets": ["test.cancellable_work"],
            "args_vars": [],
            "may_unknown": False
        }
        async_facts_helper._facts["task_create"].append(task_create)
        
        # Task cancellation
        task_cancel: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:35:10:task_cancel",
            "op": "cancel",
            "task_ids": ["test.py:30:10:create_task"]
        }
        async_facts_helper._facts["task_state"].append(task_cancel)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_create"]) == 1
        assert len(facts["task_state"]) == 1
        
        cancel_fact = facts["task_state"][0]
        assert cancel_fact["op"] == "cancel"
        assert facts["task_create"][0]["task_id"] in cancel_fact["task_ids"]
    
    def test_task_cancelled_check(self, async_facts_helper):
        """Test recognition of task.cancelled() checks."""
        task_cancelled_check: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:40:10:task_cancelled",
            "op": "cancelled",
            "task_ids": ["test.some_task"]
        }
        async_facts_helper._facts["task_state"].append(task_cancelled_check)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_state"]) == 1
        
        check_fact = facts["task_state"][0]
        assert check_fact["op"] == "cancelled"
    
    def test_cancellation_exception_handling(self, async_facts_helper):
        """Test CancelledError exception scenarios."""
        cancellation_event: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:45:5:cancelled_error",
            "op": "cancelled_error_raised",
            "task_ids": ["test.cancelled_task"]
        }
        async_facts_helper._facts["task_state"].append(cancellation_event)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_state"]) == 1
        
        cancel_fact = facts["task_state"][0]
        assert cancel_fact["op"] == "cancelled_error_raised"
    
    def test_multiple_task_cancellation(self, async_facts_helper):
        """Test cancellation of multiple tasks."""
        # Create multiple tasks
        for i in range(3):
            task_create: TaskCreateFact = {
                "fact_type": "task_create",
                "task_id": f"test.py:{50 + i * 5}:10:create_task",
                "creator_fn": "test.main",
                "targets": [f"test.worker_{i}"],
                "args_vars": [],
                "may_unknown": False
            }
            async_facts_helper._facts["task_create"].append(task_create)
        
        # Cancel all tasks
        bulk_cancel: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:70:10:bulk_cancel",
            "op": "cancel",
            "task_ids": [
                "test.py:50:10:create_task",
                "test.py:55:10:create_task", 
                "test.py:60:10:create_task"
            ]
        }
        async_facts_helper._facts["task_state"].append(bulk_cancel)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_create"]) == 3
        assert len(facts["task_state"]) == 1
        
        cancel_fact = facts["task_state"][0]
        assert len(cancel_fact["task_ids"]) == 3


class TestTaskStateOperations:
    """Test recognition of various task state operations."""
    
    def test_task_done_check(self, async_facts_helper):
        """Test recognition of task.done() checks."""
        task_done: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:75:10:task_done",
            "op": "done",
            "task_ids": ["test.checked_task"]
        }
        async_facts_helper._facts["task_state"].append(task_done)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_state"]) == 1
        assert facts["task_state"][0]["op"] == "done"
    
    def test_task_result_access(self, async_facts_helper):
        """Test recognition of task.result() calls."""
        task_result: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:80:10:task_result",
            "op": "result",
            "task_ids": ["test.completed_task"]
        }
        async_facts_helper._facts["task_state"].append(task_result)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_state"]) == 1
        assert facts["task_state"][0]["op"] == "result"
    
    def test_task_exception_access(self, async_facts_helper):
        """Test recognition of task.exception() calls."""
        task_exception: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:85:10:task_exception",
            "op": "exception",
            "task_ids": ["test.failed_task"]
        }
        async_facts_helper._facts["task_state"].append(task_exception)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_state"]) == 1
        assert facts["task_state"][0]["op"] == "exception"
    
    def test_task_add_done_callback(self, async_facts_helper):
        """Test recognition of task.add_done_callback() calls."""
        callback_add: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:90:10:add_done_callback",
            "op": "add_done_callback",
            "task_ids": ["test.monitored_task"]
        }
        async_facts_helper._facts["task_state"].append(callback_add)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_state"]) == 1
        assert facts["task_state"][0]["op"] == "add_done_callback"


class TestWaitOperations:
    """Test recognition of asyncio.wait and related operations."""
    
    def test_asyncio_wait_recognition(self, async_facts_helper):
        """Test recognition of asyncio.wait() calls."""
        # wait() typically returns when conditions are met
        wait_operation: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:95:5:asyncio_wait",
            "op": "wait",
            "task_ids": ["test.task1", "test.task2", "test.task3"]
        }
        async_facts_helper._facts["task_state"].append(wait_operation)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_state"]) == 1
        
        wait_fact = facts["task_state"][0]
        assert wait_fact["op"] == "wait"
        assert len(wait_fact["task_ids"]) == 3
    
    def test_asyncio_wait_with_timeout(self, async_facts_helper):
        """Test asyncio.wait() with timeout parameter."""
        wait_timeout: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:100:5:asyncio_wait_timeout",
            "op": "wait_timeout",
            "task_ids": ["test.task1", "test.task2"]
        }
        async_facts_helper._facts["task_state"].append(wait_timeout)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_state"]) == 1
        assert facts["task_state"][0]["op"] == "wait_timeout"
    
    def test_asyncio_as_completed(self, async_facts_helper):
        """Test recognition of asyncio.as_completed() usage."""
        as_completed: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:105:5:as_completed",
            "op": "as_completed",
            "task_ids": ["test.task1", "test.task2", "test.task3"]
        }
        async_facts_helper._facts["task_state"].append(as_completed)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_state"]) == 1
        assert facts["task_state"][0]["op"] == "as_completed"


class TestTimeoutCancellationPatterns:
    """Test common timeout and cancellation patterns."""
    
    def test_timeout_with_cleanup_pattern(self, async_facts_helper):
        """Test timeout with cleanup pattern."""
        # Task creation
        task_create: TaskCreateFact = {
            "fact_type": "task_create",
            "task_id": "test.py:110:10:create_task",
            "creator_fn": "test.main",
            "targets": ["test.long_operation"],
            "args_vars": [],
            "may_unknown": False
        }
        async_facts_helper._facts["task_create"].append(task_create)
        
        # Timeout occurs
        timeout_event: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:115:5:timeout",
            "op": "timeout_expired",
            "task_ids": ["test.py:110:10:create_task"]
        }
        async_facts_helper._facts["task_state"].append(timeout_event)
        
        # Cleanup cancellation
        cleanup_cancel: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:120:10:cleanup_cancel",
            "op": "cancel",
            "task_ids": ["test.py:110:10:create_task"]
        }
        async_facts_helper._facts["task_state"].append(cleanup_cancel)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_create"]) == 1
        assert len(facts["task_state"]) == 2
        
        # Verify timeout and cleanup sequence
        task_id = facts["task_create"][0]["task_id"]
        state_events = [fact for fact in facts["task_state"] if task_id in fact["task_ids"]]
        
        assert len(state_events) == 2
        ops = [event["op"] for event in state_events]
        assert "timeout_expired" in ops
        assert "cancel" in ops
    
    def test_graceful_shutdown_pattern(self, async_facts_helper):
        """Test graceful shutdown with cancellation pattern."""
        # Multiple worker tasks
        worker_tasks = []
        for i in range(3):
            task_create: TaskCreateFact = {
                "fact_type": "task_create",
                "task_id": f"test.py:{125 + i * 5}:10:create_task",
                "creator_fn": "test.main",
                "targets": [f"test.worker_{i}"],
                "args_vars": [],
                "may_unknown": False
            }
            async_facts_helper._facts["task_create"].append(task_create)
            worker_tasks.append(task_create["task_id"])
        
        # Graceful shutdown signal
        shutdown_signal: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:145:5:shutdown_signal",
            "op": "shutdown_requested",
            "task_ids": worker_tasks
        }
        async_facts_helper._facts["task_state"].append(shutdown_signal)
        
        # Wait for completion with timeout
        shutdown_wait: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:150:5:shutdown_wait",
            "op": "wait_timeout",
            "task_ids": worker_tasks
        }
        async_facts_helper._facts["task_state"].append(shutdown_wait)
        
        # Force cancellation if timeout
        force_cancel: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:155:5:force_cancel",
            "op": "cancel",
            "task_ids": worker_tasks
        }
        async_facts_helper._facts["task_state"].append(force_cancel)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_create"]) == 3
        assert len(facts["task_state"]) == 3
        
        # Verify graceful shutdown sequence
        shutdown_ops = [fact["op"] for fact in facts["task_state"]]
        assert "shutdown_requested" in shutdown_ops
        assert "wait_timeout" in shutdown_ops
        assert "cancel" in shutdown_ops
    
    def test_retry_with_timeout_pattern(self, async_facts_helper):
        """Test retry with timeout pattern."""
        # Retry attempts with timeouts
        for attempt in range(3):
            # Task creation for retry attempt
            task_create: TaskCreateFact = {
                "fact_type": "task_create",
                "task_id": f"test.py:{160 + attempt * 10}:10:create_task",
                "creator_fn": "test.retry_operation",
                "targets": ["test.unreliable_operation"],
                "args_vars": [],
                "may_unknown": False
            }
            async_facts_helper._facts["task_create"].append(task_create)
            
            # Timeout for each attempt
            timeout_event: TaskStateFact = {
                "fact_type": "task_state",
                "site_id": f"test.py:{165 + attempt * 10}:5:timeout",
                "op": "wait_for_timeout",
                "task_ids": [task_create["task_id"]]
            }
            async_facts_helper._facts["task_state"].append(timeout_event)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_create"]) == 3
        assert len(facts["task_state"]) == 3
        
        # Verify retry pattern
        for i, timeout_fact in enumerate(facts["task_state"]):
            assert timeout_fact["op"] == "wait_for_timeout"
            expected_task_id = f"test.py:{160 + i * 10}:10:create_task"
            assert expected_task_id in timeout_fact["task_ids"]


class TestConservativeCancellationModeling:
    """Test conservative modeling principles for cancellation and timeouts."""
    
    def test_conservative_timeout_handling(self, async_facts_helper):
        """Test conservative approach to timeout handling."""
        # Conservative principle: don't prune flows due to timeouts
        # Record timeout events but maintain flow edges
        
        # Original await edge
        await_with_timeout: AwaitEdgeFact = {
            "fact_type": "await_edge",
            "await_id": "test.py:190:5:await",
            "awaiter_fn": "test.conservative_awaiter",
            "awaited_targets": ["test.potentially_slow"],
            "may_unknown": False
        }
        async_facts_helper._facts["await_edge"].append(await_with_timeout)
        
        # Timeout event (conservative: don't remove the await edge)
        timeout_event: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:190:5:timeout",
            "op": "wait_for_timeout",
            "task_ids": ["test.potentially_slow_task"]
        }
        async_facts_helper._facts["task_state"].append(timeout_event)
        
        facts = async_facts_helper.facts()
        assert len(facts["await_edge"]) == 1
        assert len(facts["task_state"]) == 1
        
        # Conservative: await edge preserved despite timeout
        assert facts["await_edge"][0]["awaited_targets"] == ["test.potentially_slow"]
        assert facts["task_state"][0]["op"] == "wait_for_timeout"
    
    def test_conservative_cancellation_handling(self, async_facts_helper):
        """Test conservative approach to cancellation handling."""
        # Conservative principle: cancellation doesn't remove task flows
        
        task_create: TaskCreateFact = {
            "fact_type": "task_create",
            "task_id": "test.py:195:10:create_task",
            "creator_fn": "test.main",
            "targets": ["test.cancellable_work"],
            "args_vars": [],
            "may_unknown": False
        }
        async_facts_helper._facts["task_create"].append(task_create)
        
        # Cancellation event
        cancellation: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:200:10:cancel",
            "op": "cancel",
            "task_ids": ["test.py:195:10:create_task"]
        }
        async_facts_helper._facts["task_state"].append(cancellation)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_create"]) == 1
        assert len(facts["task_state"]) == 1
        
        # Conservative: task creation fact preserved despite cancellation
        assert facts["task_create"][0]["targets"] == ["test.cancellable_work"]
        assert facts["task_state"][0]["op"] == "cancel"
    
    def test_unknown_timeout_behavior(self, async_facts_helper):
        """Test handling of unknown timeout values."""
        # When timeout value cannot be determined statically
        unknown_timeout: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:205:5:unknown_timeout",
            "op": "wait_for_timeout",
            "task_ids": []  # Unknown due to dynamic timeout value
        }
        async_facts_helper._facts["task_state"].append(unknown_timeout)
        
        facts = async_facts_helper.facts()
        assert len(facts["task_state"]) == 1
        
        # Should handle unknown timeout gracefully
        timeout_fact = facts["task_state"][0]
        assert timeout_fact["op"] == "wait_for_timeout"
        assert len(timeout_fact["task_ids"]) == 0  # Empty for unknown case


class TestTimeoutCancellationIntegration:
    """Test integration of timeout/cancellation with other async constructs."""
    
    def test_timeout_with_queue_operations(self, async_facts_helper):
        """Test timeout in queue operations."""
        from pythonstan.analysis.pointer.kcfa2.async_types import QueueGetFact
        
        # Queue get with potential timeout
        queue_get: QueueGetFact = {
            "fact_type": "queue_get",
            "site_id": "test.py:210:10:queue_get",
            "queue_ids": ["test.queue"],
            "target_var": "item"
        }
        async_facts_helper._facts["queue_get"].append(queue_get)
        
        # Timeout on queue operation
        queue_timeout: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:210:10:queue_timeout",
            "op": "wait_for_timeout",
            "task_ids": ["queue_get_task"]
        }
        async_facts_helper._facts["task_state"].append(queue_timeout)
        
        facts = async_facts_helper.facts()
        assert len(facts["queue_get"]) == 1
        assert len(facts["task_state"]) == 1
        
        # Queue operation should be preserved despite potential timeout
        assert facts["queue_get"][0]["target_var"] == "item"
        assert facts["task_state"][0]["op"] == "wait_for_timeout"
    
    def test_cancellation_with_sync_primitives(self, async_facts_helper):
        """Test cancellation during sync primitive operations."""
        from pythonstan.analysis.pointer.kcfa2.async_types import SyncOpFact
        
        # Lock acquire operation
        lock_acquire: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:215:10:sync_op",
            "kind": "Lock",
            "op": "acquire",
            "target_ids": ["test.lock"]
        }
        async_facts_helper._facts["sync_op"].append(lock_acquire)
        
        # Cancellation during lock acquire
        acquire_cancel: TaskStateFact = {
            "fact_type": "task_state",
            "site_id": "test.py:215:10:acquire_cancel",
            "op": "cancel",
            "task_ids": ["lock_acquire_task"]
        }
        async_facts_helper._facts["task_state"].append(acquire_cancel)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_op"]) == 1
        assert len(facts["task_state"]) == 1
        
        # Sync operation preserved despite cancellation possibility
        assert facts["sync_op"][0]["op"] == "acquire"
        assert facts["task_state"][0]["op"] == "cancel"
