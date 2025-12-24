"""Delta tracker for iteration-aware debugging.

Tracks events across iterations and computes deltas between iteration k and
the current iteration. Designed for minimal overhead with configurable limits.
"""

import threading
from collections import defaultdict
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field

from .models import DeltaEvent, DeltaEventType, DeltaSummary


class DeltaTracker:
    """Tracks analysis events for computing iteration deltas.
    
    Uses event-driven tracking where each event is tagged with its iteration.
    Deltas are computed by aggregating events between iter-k+1 and iter.
    
    Thread-safe for concurrent event recording.
    """
    
    def __init__(
        self,
        max_events_per_type: int = 50000,
        max_total_events: int = 200000
    ):
        """Initialize delta tracker.
        
        Args:
            max_events_per_type: Maximum events to keep per event type
            max_total_events: Maximum total events across all types
        """
        self.max_events_per_type = max_events_per_type
        self.max_total_events = max_total_events
        
        # Event storage by type and iteration
        self._events: Dict[DeltaEventType, List[DeltaEvent]] = defaultdict(list)
        self._events_by_iteration: Dict[int, List[DeltaEvent]] = defaultdict(list)
        
        # Current iteration (updated by solver hook)
        self._current_iteration = 0
        
        # Lock for thread safety
        self._lock = threading.Lock()
        
        # Listeners for real-time updates
        self._listeners: List[Callable[[DeltaEvent], None]] = []
        
        # Counters for statistics
        self._total_events = 0
        self._dropped_events = 0
    
    @property
    def current_iteration(self) -> int:
        """Get current iteration."""
        return self._current_iteration
    
    def set_iteration(self, iteration: int) -> None:
        """Set current iteration. Called by solver hook."""
        with self._lock:
            self._current_iteration = iteration
    
    def add_listener(self, listener: Callable[[DeltaEvent], None]) -> None:
        """Add a listener for real-time event notifications."""
        self._listeners.append(listener)
    
    def remove_listener(self, listener: Callable[[DeltaEvent], None]) -> None:
        """Remove a listener."""
        if listener in self._listeners:
            self._listeners.remove(listener)
    
    def record_event(self, event_type: DeltaEventType, data: Dict[str, Any]) -> None:
        """Record an event with the current iteration.
        
        Args:
            event_type: Type of event
            data: Event data (should be serializable strings/dicts/lists)
        """
        with self._lock:
            # Check limits
            if self._total_events >= self.max_total_events:
                self._dropped_events += 1
                return
            
            type_events = self._events[event_type]
            if len(type_events) >= self.max_events_per_type:
                self._dropped_events += 1
                return
            
            event = DeltaEvent(
                event_type=event_type,
                iteration=self._current_iteration,
                data=data
            )
            
            self._events[event_type].append(event)
            self._events_by_iteration[self._current_iteration].append(event)
            self._total_events += 1
        
        # Notify listeners (outside lock)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass  # Don't let listener errors break recording
    
    def record_points_to_update(
        self,
        variable: str,
        old_size: int,
        new_size: int,
        added_objects: List[str]
    ) -> None:
        """Record a points-to set update."""
        self.record_event(DeltaEventType.POINTS_TO_UPDATE, {
            'variable': variable,
            'old_size': old_size,
            'new_size': new_size,
            'added_count': len(added_objects),
            'added_objects': added_objects[:10]  # Limit stored objects
        })
    
    def record_call_edge_created(
        self,
        caller: str,
        callee: str,
        call_site: str,
        callee_type: str
    ) -> None:
        """Record a new call edge."""
        self.record_event(DeltaEventType.CALL_EDGE_CREATED, {
            'caller': caller,
            'callee': callee,
            'call_site': call_site,
            'callee_type': callee_type
        })
    
    def record_call_failed(
        self,
        call_site: str,
        reason: str,
        details: str
    ) -> None:
        """Record a failed call resolution."""
        self.record_event(DeltaEventType.CALL_FAILED, {
            'call_site': call_site,
            'reason': reason,
            'details': details
        })
    
    def record_constraint_applied(
        self,
        constraint_type: str,
        constraint_str: str
    ) -> None:
        """Record a constraint application."""
        self.record_event(DeltaEventType.CONSTRAINT_APPLIED, {
            'type': constraint_type,
            'constraint': constraint_str[:200]  # Limit string length
        })
    
    def record_pfg_edge_activated(
        self,
        edge_id: str,
        num_objects: int
    ) -> None:
        """Record a PFG edge activation."""
        self.record_event(DeltaEventType.PFG_EDGE_ACTIVATED, {
            'edge_id': edge_id,
            'num_objects': num_objects
        })
    
    def record_scope_analyzed(
        self,
        scope_qualname: str,
        context: str
    ) -> None:
        """Record a newly analyzed scope."""
        self.record_event(DeltaEventType.SCOPE_ANALYZED, {
            'scope': scope_qualname,
            'context': context
        })
    
    def record_object_allocated(
        self,
        obj_str: str,
        obj_kind: str,
        location: str
    ) -> None:
        """Record an object allocation."""
        self.record_event(DeltaEventType.OBJECT_ALLOCATED, {
            'object': obj_str[:200],
            'kind': obj_kind,
            'location': location
        })
    
    def compute_delta(self, from_iteration: int) -> DeltaSummary:
        """Compute delta from iteration k to current iteration.
        
        Args:
            from_iteration: Starting iteration (exclusive)
            
        Returns:
            DeltaSummary with all changes since from_iteration
        """
        with self._lock:
            to_iteration = self._current_iteration
            
            # Collect events in the iteration range
            relevant_events: Dict[DeltaEventType, List[DeltaEvent]] = defaultdict(list)
            
            for iteration in range(from_iteration + 1, to_iteration + 1):
                for event in self._events_by_iteration.get(iteration, []):
                    relevant_events[event.event_type].append(event)
            
            # Build delta summary
            points_to_additions = [
                e.data for e in relevant_events[DeltaEventType.POINTS_TO_UPDATE]
            ]
            
            new_call_edges = [
                e.data for e in relevant_events[DeltaEventType.CALL_EDGE_CREATED]
            ]
            
            new_call_failures = [
                e.data for e in relevant_events[DeltaEventType.CALL_FAILED]
            ]
            
            # Aggregate constraint counts
            constraint_counts: Dict[str, int] = defaultdict(int)
            constraint_examples: List[str] = []
            for event in relevant_events[DeltaEventType.CONSTRAINT_APPLIED]:
                ctype = event.data.get('type', 'unknown')
                constraint_counts[ctype] += 1
                if len(constraint_examples) < 20:
                    constraint_examples.append(event.data.get('constraint', ''))
            
            new_scopes = list(set(
                e.data.get('scope', '') 
                for e in relevant_events[DeltaEventType.SCOPE_ANALYZED]
            ))
            
            # PFG activity aggregation
            pfg_activations = len(relevant_events[DeltaEventType.PFG_EDGE_ACTIVATED])
            pfg_object_flow = sum(
                e.data.get('num_objects', 0) 
                for e in relevant_events[DeltaEventType.PFG_EDGE_ACTIVATED]
            )
            
            return DeltaSummary(
                from_iteration=from_iteration,
                to_iteration=to_iteration,
                points_to_additions=points_to_additions,
                new_call_edges=new_call_edges,
                new_call_failures=new_call_failures,
                constraints_applied_counts=dict(constraint_counts),
                constraint_examples=constraint_examples,
                new_scopes_analyzed=new_scopes,
                pfg_activations=pfg_activations,
                pfg_object_flow=pfg_object_flow
            )
    
    def get_events_in_range(
        self,
        event_type: DeltaEventType,
        from_iteration: int,
        to_iteration: Optional[int] = None
    ) -> List[DeltaEvent]:
        """Get all events of a type in an iteration range.
        
        Args:
            event_type: Type of events to retrieve
            from_iteration: Starting iteration (exclusive)
            to_iteration: Ending iteration (inclusive), defaults to current
            
        Returns:
            List of events in the range
        """
        with self._lock:
            if to_iteration is None:
                to_iteration = self._current_iteration
            
            result = []
            for event in self._events[event_type]:
                if from_iteration < event.iteration <= to_iteration:
                    result.append(event)
            
            return result
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get tracker statistics."""
        with self._lock:
            return {
                'current_iteration': self._current_iteration,
                'total_events': self._total_events,
                'dropped_events': self._dropped_events,
                'events_by_type': {
                    etype.value: len(events) 
                    for etype, events in self._events.items()
                },
                'iterations_tracked': len(self._events_by_iteration)
            }
    
    def clear(self) -> None:
        """Clear all tracked events."""
        with self._lock:
            self._events.clear()
            self._events_by_iteration.clear()
            self._current_iteration = 0
            self._total_events = 0
            self._dropped_events = 0


class DeltaTrackerMonitorAdapter:
    """Adapter that bridges DebugMonitor events to DeltaTracker.
    
    Install this as a subclass or wrapper around DebugMonitor to forward
    events to the DeltaTracker.
    """
    
    def __init__(self, delta_tracker: DeltaTracker):
        """Initialize adapter.
        
        Args:
            delta_tracker: DeltaTracker to forward events to
        """
        self.tracker = delta_tracker
    
    def set_iteration(self, iteration: int) -> None:
        """Forward iteration updates."""
        self.tracker.set_iteration(iteration)
    
    def record_points_to_update(
        self,
        variable_str: str,
        old_size: int,
        new_size: int,
        added_objects: List[str]
    ) -> None:
        """Forward points-to updates."""
        self.tracker.record_points_to_update(
            variable_str, old_size, new_size, added_objects
        )
    
    def record_call_edge_created(
        self,
        caller: str,
        callee: str,
        call_site: str,
        callee_type: str
    ) -> None:
        """Forward call edge creation."""
        self.tracker.record_call_edge_created(caller, callee, call_site, callee_type)
    
    def record_call_failed(
        self,
        call_site: str,
        reason: str,
        details: str
    ) -> None:
        """Forward call failures."""
        self.tracker.record_call_failed(call_site, reason, details)
    
    def record_constraint_applied(
        self,
        constraint_type: str,
        constraint_str: str
    ) -> None:
        """Forward constraint applications."""
        self.tracker.record_constraint_applied(constraint_type, constraint_str)
    
    def record_pfg_edge_activated(
        self,
        edge_id: str,
        num_objects: int
    ) -> None:
        """Forward PFG activations."""
        self.tracker.record_pfg_edge_activated(edge_id, num_objects)


class DeltaTrackerObserver:
    """Observer that implements MonitorObserver interface for DeltaTracker.
    
    This is the proper integration point - add this observer to a DebugMonitor
    to automatically forward events to the DeltaTracker.
    
    Usage:
        delta_tracker = DeltaTracker()
        observer = DeltaTrackerObserver(delta_tracker)
        debug_monitor.add_observer(observer)
    """
    
    def __init__(self, delta_tracker: DeltaTracker):
        """Initialize observer.
        
        Args:
            delta_tracker: DeltaTracker to forward events to
        """
        self.tracker = delta_tracker
    
    def on_iteration_changed(self, iteration: int) -> None:
        """Called when iteration counter changes."""
        self.tracker.set_iteration(iteration)
    
    def on_points_to_update(
        self,
        variable_str: str,
        old_size: int,
        new_size: int,
        added_objects: List[str]
    ) -> None:
        """Called when a points-to set is updated."""
        self.tracker.record_points_to_update(variable_str, old_size, new_size, added_objects)
    
    def on_call_edge_created(
        self,
        caller: str,
        callee: str,
        call_site: str,
        callee_type: str
    ) -> None:
        """Called when a new call edge is created."""
        self.tracker.record_call_edge_created(caller, callee, call_site, callee_type)
    
    def on_call_failed(
        self,
        call_site: str,
        reason: str,
        details: str
    ) -> None:
        """Called when call resolution fails."""
        self.tracker.record_call_failed(call_site, reason, details)
    
    def on_constraint_applied(
        self,
        constraint_type: str,
        constraint_str: str
    ) -> None:
        """Called when a constraint is applied."""
        self.tracker.record_constraint_applied(constraint_type, constraint_str)
    
    def on_pfg_edge_activated(
        self,
        edge_id: str,
        num_objects: int
    ) -> None:
        """Called when a PFG edge is activated."""
        self.tracker.record_pfg_edge_activated(edge_id, num_objects)
    
    def on_scope_analyzed(
        self,
        scope_qualname: str,
        context: str
    ) -> None:
        """Called when a new scope is analyzed."""
        self.tracker.record_scope_analyzed(scope_qualname, context)
    
    def on_object_allocated(
        self,
        obj_id: str,
        obj_kind: str,
        location: str,
        target_var: Optional[str]
    ) -> None:
        """Called when an object is allocated."""
        self.tracker.record_object_allocated(obj_id, obj_kind, location)

