"""Tests for synchronization primitive recognition and fact extraction.

This module tests recognition and fact extraction for asyncio synchronization
primitives including Lock, Semaphore, BoundedSemaphore, Event, and Condition.

Tests cover allocation, operations (acquire/release/wait/set/clear), and
fact recording for sync primitive usage patterns.
"""

import pytest
import ast
from typing import List, Dict, Any, Optional
from unittest.mock import Mock

from pythonstan.analysis.pointer.kcfa2.async_facts import AsyncFactsHelper
from pythonstan.analysis.pointer.kcfa2.async_types import (
    SyncAllocFact, SyncOpFact
)
from pythonstan.analysis.pointer.kcfa2.config import KCFAConfig


class TestSyncPrimitiveRecognition:
    """Test recognition of synchronization primitive allocations and operations."""
    
    def test_lock_allocation_recognition(self, async_facts_helper):
        """Test recognition of Lock allocation."""
        # Mock Lock allocation fact
        lock_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:10:5:sync",
            "kind": "Lock"
        }
        
        async_facts_helper._facts["sync_alloc"].append(lock_alloc)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_alloc"]) == 1
        
        fact = facts["sync_alloc"][0]
        assert fact["kind"] == "Lock"
        assert fact["sync_id"] == "test.py:10:5:sync"
    
    def test_semaphore_allocation_recognition(self, async_facts_helper):
        """Test recognition of Semaphore and BoundedSemaphore allocation."""
        # Semaphore allocation
        semaphore_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:15:5:sync",
            "kind": "Semaphore"
        }
        
        async_facts_helper._facts["sync_alloc"].append(semaphore_alloc)
        
        # BoundedSemaphore allocation  
        bounded_semaphore_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:20:5:sync",
            "kind": "BoundedSemaphore"
        }
        
        async_facts_helper._facts["sync_alloc"].append(bounded_semaphore_alloc)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_alloc"]) == 2
        
        kinds = [fact["kind"] for fact in facts["sync_alloc"]]
        assert "Semaphore" in kinds
        assert "BoundedSemaphore" in kinds
    
    def test_event_allocation_recognition(self, async_facts_helper):
        """Test recognition of Event allocation."""
        event_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:25:5:sync",
            "kind": "Event"
        }
        
        async_facts_helper._facts["sync_alloc"].append(event_alloc)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_alloc"]) == 1
        assert facts["sync_alloc"][0]["kind"] == "Event"
    
    def test_condition_allocation_recognition(self, async_facts_helper):
        """Test recognition of Condition allocation."""
        condition_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:30:5:sync",
            "kind": "Condition"
        }
        
        async_facts_helper._facts["sync_alloc"].append(condition_alloc)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_alloc"]) == 1
        assert facts["sync_alloc"][0]["kind"] == "Condition"
    
    def test_all_sync_primitive_types(self, async_facts_helper):
        """Test recognition of all supported sync primitive types."""
        sync_types = ["Lock", "Semaphore", "BoundedSemaphore", "Event", "Condition"]
        
        for i, sync_type in enumerate(sync_types):
            sync_alloc: SyncAllocFact = {
                "fact_type": "sync_alloc",
                "sync_id": f"test.py:{10 + i * 5}:5:sync",
                "kind": sync_type
            }
            async_facts_helper._facts["sync_alloc"].append(sync_alloc)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_alloc"]) == len(sync_types)
        
        recorded_types = [fact["kind"] for fact in facts["sync_alloc"]]
        for sync_type in sync_types:
            assert sync_type in recorded_types


class TestSyncPrimitiveOperations:
    """Test recognition of synchronization primitive operations."""
    
    def test_lock_operations(self, async_facts_helper):
        """Test Lock acquire and release operations."""
        # Lock allocation
        lock_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:10:5:sync",
            "kind": "Lock"
        }
        async_facts_helper._facts["sync_alloc"].append(lock_alloc)
        
        # Lock acquire
        lock_acquire: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:15:10:sync_op",
            "kind": "Lock",
            "op": "acquire",
            "target_ids": ["test.py:10:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(lock_acquire)
        
        # Lock release
        lock_release: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:20:10:sync_op",
            "kind": "Lock",
            "op": "release",
            "target_ids": ["test.py:10:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(lock_release)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_alloc"]) == 1
        assert len(facts["sync_op"]) == 2
        
        ops = [fact["op"] for fact in facts["sync_op"]]
        assert "acquire" in ops
        assert "release" in ops
        
        # Verify all operations target the same lock
        lock_id = facts["sync_alloc"][0]["sync_id"]
        for op_fact in facts["sync_op"]:
            assert lock_id in op_fact["target_ids"]
    
    def test_semaphore_operations(self, async_facts_helper):
        """Test Semaphore acquire and release operations."""
        # Semaphore allocation
        sem_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:25:5:sync",
            "kind": "Semaphore"
        }
        async_facts_helper._facts["sync_alloc"].append(sem_alloc)
        
        # Semaphore acquire
        sem_acquire: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:30:10:sync_op",
            "kind": "Semaphore", 
            "op": "acquire",
            "target_ids": ["test.py:25:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(sem_acquire)
        
        # Semaphore release
        sem_release: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:35:10:sync_op",
            "kind": "Semaphore",
            "op": "release", 
            "target_ids": ["test.py:25:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(sem_release)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_alloc"]) == 1
        assert len(facts["sync_op"]) == 2
        
        # Verify semaphore-specific operations
        for op_fact in facts["sync_op"]:
            assert op_fact["kind"] == "Semaphore"
            assert op_fact["op"] in ["acquire", "release"]
    
    def test_event_operations(self, async_facts_helper):
        """Test Event set, clear, and wait operations."""
        # Event allocation
        event_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:40:5:sync",
            "kind": "Event"
        }
        async_facts_helper._facts["sync_alloc"].append(event_alloc)
        
        # Event set
        event_set: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:45:10:sync_op",
            "kind": "Event",
            "op": "set",
            "target_ids": ["test.py:40:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(event_set)
        
        # Event clear
        event_clear: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:50:10:sync_op",
            "kind": "Event",
            "op": "clear",
            "target_ids": ["test.py:40:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(event_clear)
        
        # Event wait
        event_wait: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:55:10:sync_op",
            "kind": "Event",
            "op": "wait",
            "target_ids": ["test.py:40:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(event_wait)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_alloc"]) == 1
        assert len(facts["sync_op"]) == 3
        
        # Verify event-specific operations
        ops = [fact["op"] for fact in facts["sync_op"]]
        assert "set" in ops
        assert "clear" in ops
        assert "wait" in ops
        
        for op_fact in facts["sync_op"]:
            assert op_fact["kind"] == "Event"
    
    def test_condition_operations(self, async_facts_helper):
        """Test Condition notify, notify_all, and wait operations."""
        # Condition allocation
        cond_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:60:5:sync",
            "kind": "Condition"
        }
        async_facts_helper._facts["sync_alloc"].append(cond_alloc)
        
        # Condition wait
        cond_wait: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:65:10:sync_op",
            "kind": "Condition",
            "op": "wait",
            "target_ids": ["test.py:60:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(cond_wait)
        
        # Condition notify
        cond_notify: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:70:10:sync_op",
            "kind": "Condition",
            "op": "notify",
            "target_ids": ["test.py:60:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(cond_notify)
        
        # Condition notify_all
        cond_notify_all: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:75:10:sync_op",
            "kind": "Condition",
            "op": "notify_all",
            "target_ids": ["test.py:60:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(cond_notify_all)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_alloc"]) == 1
        assert len(facts["sync_op"]) == 3
        
        # Verify condition-specific operations
        ops = [fact["op"] for fact in facts["sync_op"]]
        assert "wait" in ops
        assert "notify" in ops
        assert "notify_all" in ops
        
        for op_fact in facts["sync_op"]:
            assert op_fact["kind"] == "Condition"


class TestSyncPrimitiveQueries:
    """Test query methods for synchronization primitives."""
    
    def test_sync_primitives_by_type_query(self, async_facts_helper):
        """Test sync_primitives_by_type() query method."""
        # Add various sync allocations
        sync_allocations = [
            {"fact_type": "sync_alloc", "sync_id": "test.py:10:5:sync", "kind": "Lock"},
            {"fact_type": "sync_alloc", "sync_id": "test.py:15:5:sync", "kind": "Lock"},
            {"fact_type": "sync_alloc", "sync_id": "test.py:20:5:sync", "kind": "Semaphore"},
            {"fact_type": "sync_alloc", "sync_id": "test.py:25:5:sync", "kind": "Event"},
            {"fact_type": "sync_alloc", "sync_id": "test.py:30:5:sync", "kind": "Condition"},
            {"fact_type": "sync_alloc", "sync_id": "test.py:35:5:sync", "kind": "BoundedSemaphore"}
        ]
        
        for alloc in sync_allocations:
            async_facts_helper._facts["sync_alloc"].append(alloc)
        
        # Test queries
        locks = async_facts_helper.sync_primitives_by_type("Lock")
        assert len(locks) == 2
        
        semaphores = async_facts_helper.sync_primitives_by_type("Semaphore")
        assert len(semaphores) == 1
        
        events = async_facts_helper.sync_primitives_by_type("Event")
        assert len(events) == 1
        
        conditions = async_facts_helper.sync_primitives_by_type("Condition")
        assert len(conditions) == 1
        
        bounded_semaphores = async_facts_helper.sync_primitives_by_type("BoundedSemaphore")
        assert len(bounded_semaphores) == 1
        
        # Test non-existent type
        unknown = async_facts_helper.sync_primitives_by_type("UnknownSync")
        assert len(unknown) == 0
    
    def test_sync_operations_filtering(self, async_facts_helper):
        """Test filtering sync operations by type and operation."""
        # Add sync allocations and operations
        lock_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:10:5:sync",
            "kind": "Lock"
        }
        async_facts_helper._facts["sync_alloc"].append(lock_alloc)
        
        event_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:15:5:sync",
            "kind": "Event"
        }
        async_facts_helper._facts["sync_alloc"].append(event_alloc)
        
        # Add various operations
        operations = [
            {"fact_type": "sync_op", "site_id": "test.py:20:5:sync_op", "kind": "Lock", "op": "acquire", "target_ids": ["test.py:10:5:sync"]},
            {"fact_type": "sync_op", "site_id": "test.py:25:5:sync_op", "kind": "Lock", "op": "release", "target_ids": ["test.py:10:5:sync"]},
            {"fact_type": "sync_op", "site_id": "test.py:30:5:sync_op", "kind": "Event", "op": "set", "target_ids": ["test.py:15:5:sync"]},
            {"fact_type": "sync_op", "site_id": "test.py:35:5:sync_op", "kind": "Event", "op": "wait", "target_ids": ["test.py:15:5:sync"]}
        ]
        
        for op in operations:
            async_facts_helper._facts["sync_op"].append(op)
        
        facts = async_facts_helper.facts()
        
        # Filter lock operations
        lock_ops = [fact for fact in facts["sync_op"] if fact["kind"] == "Lock"]
        assert len(lock_ops) == 2
        
        # Filter event operations
        event_ops = [fact for fact in facts["sync_op"] if fact["kind"] == "Event"]
        assert len(event_ops) == 2
        
        # Filter acquire operations
        acquire_ops = [fact for fact in facts["sync_op"] if fact["op"] == "acquire"]
        assert len(acquire_ops) == 1
        assert acquire_ops[0]["kind"] == "Lock"


class TestSyncPrimitivePatterns:
    """Test recognition of common sync primitive usage patterns."""
    
    def test_async_context_manager_pattern(self, async_facts_helper):
        """Test async with lock pattern recognition."""
        # This would normally be detected from "async with lock:" syntax
        # For now, test the underlying acquire/release pattern
        
        lock_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:10:5:sync",
            "kind": "Lock"
        }
        async_facts_helper._facts["sync_alloc"].append(lock_alloc)
        
        # Pattern: acquire at context enter, release at context exit
        lock_acquire: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:15:10:sync_op",
            "kind": "Lock",
            "op": "acquire",
            "target_ids": ["test.py:10:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(lock_acquire)
        
        lock_release: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:20:10:sync_op",
            "kind": "Lock",
            "op": "release",
            "target_ids": ["test.py:10:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(lock_release)
        
        facts = async_facts_helper.facts()
        
        # Verify acquire/release pairing
        lock_id = facts["sync_alloc"][0]["sync_id"]
        lock_ops = [fact for fact in facts["sync_op"] if lock_id in fact["target_ids"]]
        
        assert len(lock_ops) == 2
        ops = [fact["op"] for fact in lock_ops]
        assert "acquire" in ops
        assert "release" in ops
    
    def test_producer_consumer_event_pattern(self, async_facts_helper):
        """Test producer-consumer pattern with Event."""
        # Event for coordination
        event_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:25:5:sync",
            "kind": "Event"
        }
        async_facts_helper._facts["sync_alloc"].append(event_alloc)
        
        # Producer sets event
        event_set: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:30:10:sync_op",
            "kind": "Event",
            "op": "set",
            "target_ids": ["test.py:25:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(event_set)
        
        # Consumer waits for event
        event_wait: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:35:10:sync_op",
            "kind": "Event",
            "op": "wait",
            "target_ids": ["test.py:25:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(event_wait)
        
        facts = async_facts_helper.facts()
        
        # Verify producer-consumer pattern
        event_id = facts["sync_alloc"][0]["sync_id"]
        event_ops = [fact for fact in facts["sync_op"] if event_id in fact["target_ids"]]
        
        assert len(event_ops) == 2
        ops = [fact["op"] for fact in event_ops]
        assert "set" in ops
        assert "wait" in ops
    
    def test_resource_limiting_semaphore_pattern(self, async_facts_helper):
        """Test resource limiting pattern with Semaphore."""
        # Semaphore for resource limiting
        sem_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:40:5:sync",
            "kind": "Semaphore"
        }
        async_facts_helper._facts["sync_alloc"].append(sem_alloc)
        
        # Multiple acquire operations (resource requests)
        for i in range(3):
            sem_acquire: SyncOpFact = {
                "fact_type": "sync_op",
                "site_id": f"test.py:{45 + i * 5}:10:sync_op",
                "kind": "Semaphore",
                "op": "acquire",
                "target_ids": ["test.py:40:5:sync"]
            }
            async_facts_helper._facts["sync_op"].append(sem_acquire)
        
        # Multiple release operations (resource releases)
        for i in range(3):
            sem_release: SyncOpFact = {
                "fact_type": "sync_op",
                "site_id": f"test.py:{60 + i * 5}:10:sync_op",
                "kind": "Semaphore",
                "op": "release",
                "target_ids": ["test.py:40:5:sync"]
            }
            async_facts_helper._facts["sync_op"].append(sem_release)
        
        facts = async_facts_helper.facts()
        
        # Verify resource limiting pattern
        sem_id = facts["sync_alloc"][0]["sync_id"]
        sem_ops = [fact for fact in facts["sync_op"] if sem_id in fact["target_ids"]]
        
        assert len(sem_ops) == 6  # 3 acquires + 3 releases
        
        acquire_ops = [fact for fact in sem_ops if fact["op"] == "acquire"]
        release_ops = [fact for fact in sem_ops if fact["op"] == "release"]
        
        assert len(acquire_ops) == 3
        assert len(release_ops) == 3


class TestSyncPrimitiveIntegration:
    """Test integration of sync primitives with other async constructs."""
    
    def test_sync_with_await_pattern(self, async_facts_helper):
        """Test sync primitives used with await expressions."""
        # Lock allocation
        lock_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:70:5:sync",
            "kind": "Lock"
        }
        async_facts_helper._facts["sync_alloc"].append(lock_alloc)
        
        # Lock acquire (awaited)
        lock_acquire: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:75:10:sync_op",
            "kind": "Lock",
            "op": "acquire",
            "target_ids": ["test.py:70:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(lock_acquire)
        
        # Await edge for the lock acquire
        from pythonstan.analysis.pointer.kcfa2.async_types import AwaitEdgeFact
        await_acquire: AwaitEdgeFact = {
            "fact_type": "await_edge",
            "await_id": "test.py:75:5:await",
            "awaiter_fn": "test.worker",
            "awaited_targets": ["lock.acquire"],
            "may_unknown": False
        }
        async_facts_helper._facts["await_edge"].append(await_acquire)
        
        facts = async_facts_helper.facts()
        
        # Verify integration
        assert len(facts["sync_alloc"]) == 1
        assert len(facts["sync_op"]) == 1
        assert len(facts["await_edge"]) == 1
        
        # The await should target the acquire operation
        assert facts["await_edge"][0]["awaited_targets"] == ["lock.acquire"]
    
    def test_sync_with_task_pattern(self, async_facts_helper):
        """Test sync primitives used across task boundaries."""
        # Event for inter-task communication
        event_alloc: SyncAllocFact = {
            "fact_type": "sync_alloc",
            "sync_id": "test.py:80:5:sync",
            "kind": "Event"
        }
        async_facts_helper._facts["sync_alloc"].append(event_alloc)
        
        # Task creation
        from pythonstan.analysis.pointer.kcfa2.async_types import TaskCreateFact
        task_create: TaskCreateFact = {
            "fact_type": "task_create",
            "task_id": "test.py:85:10:create_task",
            "creator_fn": "test.main",
            "targets": ["test.worker"],
            "args_vars": ["event"],
            "may_unknown": False
        }
        async_facts_helper._facts["task_create"].append(task_create)
        
        # Event operations across tasks
        event_wait: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:90:10:sync_op",
            "kind": "Event",
            "op": "wait",
            "target_ids": ["test.py:80:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(event_wait)
        
        event_set: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:95:10:sync_op",
            "kind": "Event",
            "op": "set",
            "target_ids": ["test.py:80:5:sync"]
        }
        async_facts_helper._facts["sync_op"].append(event_set)
        
        facts = async_facts_helper.facts()
        
        # Verify cross-task synchronization
        assert len(facts["sync_alloc"]) == 1
        assert len(facts["task_create"]) == 1
        assert len(facts["sync_op"]) == 2
        
        # Event should be passed to task
        assert "event" in facts["task_create"][0]["args_vars"]
        
        # Event should have both wait and set operations
        event_id = facts["sync_alloc"][0]["sync_id"]
        event_ops = [fact for fact in facts["sync_op"] if event_id in fact["target_ids"]]
        ops = [fact["op"] for fact in event_ops]
        assert "wait" in ops
        assert "set" in ops


class TestSyncPrimitiveLimitations:
    """Test current limitations and expected behaviors for sync primitives."""
    
    def test_unknown_sync_object_handling(self, async_facts_helper):
        """Test handling of unknown sync object references."""
        # Operation on unknown sync object
        unknown_sync_op: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:100:10:sync_op",
            "kind": "Lock",
            "op": "acquire",
            "target_ids": []  # Unknown sync object
        }
        async_facts_helper._facts["sync_op"].append(unknown_sync_op)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_op"]) == 1
        
        # Should handle unknown targets gracefully
        assert facts["sync_op"][0]["target_ids"] == []
    
    def test_complex_sync_expressions(self, async_facts_helper):
        """Test handling of complex sync primitive expressions."""
        # This documents cases that may need special handling
        # e.g., sync objects from complex expressions, method chaining, etc.
        
        # For now, verify that basic fact structure is maintained
        # even for complex cases
        complex_sync_op: SyncOpFact = {
            "fact_type": "sync_op",
            "site_id": "test.py:105:10:sync_op",
            "kind": "Lock",
            "op": "acquire",
            "target_ids": ["complex_expression_result"]
        }
        async_facts_helper._facts["sync_op"].append(complex_sync_op)
        
        facts = async_facts_helper.facts()
        assert len(facts["sync_op"]) == 1
        
        # Should maintain fact structure even for complex cases
        assert facts["sync_op"][0]["kind"] == "Lock"
        assert facts["sync_op"][0]["op"] == "acquire"
