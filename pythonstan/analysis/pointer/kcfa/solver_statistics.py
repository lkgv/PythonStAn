"""The actual finite Native solver and points-queue statistics records.

These are the existing mutable dictionaries, including partial read-only query
fixtures. Their field types describe observations, not abstract program facts.
"""

from typing import TypedDict


class NativeQueueStatistics(TypedDict):
    points_work_policy: str
    hot_quota: int
    pending_hot: int
    pending_cold: int
    hot_pops: int
    cold_pops: int
    promotions: int
    cold_seed_nodes: int


class NativeSolverStatistics(TypedDict, total=False):
    iterations: int
    constraints_applied: int
    converged: int
    iteration_limit_reached: int
    ai_intrinsic_attempts: int
    ai_intrinsic_applied: int
    ai_intrinsic_fallbacks: int
    ai_intrinsic_deferred: int
    builtin_dependency_replays: int
    work_items_static: int
    work_items_constraint_replay: int
    work_items_builtin_replay: int
    work_items_points_to: int
    points_to_noop_work_items: int
    points_to_delta_objects: int
    builtin_replay_notifications: int
    builtin_replay_notifications_coalesced: int
    points_to_enqueue_processed_skips: int
    points_to_enqueue_trimmed_requests: int
    work_scheduling_policy: str
    points_work_policy: str
    work_scheduler_kind_order: list[str]
    work_scheduler_cursor: int
    work_kind_first_service_iteration: dict[str, int | None]
    configuration_diagnostics: list[str]
    initial_pending_work_by_kind: dict[str, int]
    pending_work: int
    pending_work_by_kind: dict[str, int]
    points_queue_points_work_policy: str
    points_queue_hot_quota: int
    points_queue_pending_hot: int
    points_queue_pending_cold: int
    points_queue_hot_pops: int
    points_queue_cold_pops: int
    points_queue_promotions: int
    points_queue_cold_seed_nodes: int
