"""Fixed hooks for optional, externally supplied Native debug monitors.

A partial monitor is valid until a particular enabled operation needs a hook.
The raw monitor and callback identities are retained; lookup and Python argument
binding happen at the original use site. Callback results are opaque and ignored.
"""

import importlib
from enum import Enum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .pointer_flow_graph import PointerFlowGraph
    from .state import PointerAnalysisState

type DebugFeature = Literal["enabled", "track_object_flow", "track_pfg"]


def feature(monitor: object, name: DebugFeature) -> bool:
    value: object = getattr(monitor, name)
    return bool(value)


def _callback(
    monitor: object,
    name: Literal[
        "set_iteration",
        "record_iteration_snapshot",
        "record_object_allocated",
        "record_call_constraint_processed",
        "record_call_failed",
        "record_points_to_update",
        "record_pfg_edge_activated",
        "record_call_edge_created",
        "compute_points_to_statistics",
        "compute_pfg_statistics",
        "export_to_json",
        "generate_summary_report",
    ],
) -> object:
    callback: object = getattr(monitor, name)
    return callback


# This is an opaque external callback boundary: callability is checked at
# the real use site and Python binds the exact original arguments. The ignored
# return is captured as object; no signature or monitor implementation is assumed.
def set_iteration(monitor: object, iteration: int) -> None:
    callback = _callback(monitor, "set_iteration")
    if not callable(callback):
        raise TypeError("Debug monitor hook is not callable")
    _ignored: object = callback(iteration)


def record_iteration_snapshot(
    monitor: object,
    *,
    worklist_size: int,
    call_edges: int,
    pfg_edges: int,
    num_variables: int,
    num_objects: int,
) -> None:
    callback = _callback(monitor, "record_iteration_snapshot")
    if not callable(callback):
        raise TypeError("Debug monitor hook is not callable")
    _ignored: object = callback(
        worklist_size=worklist_size,
        call_edges=call_edges,
        pfg_edges=pfg_edges,
        num_variables=num_variables,
        num_objects=num_objects,
    )


def record_object_allocated(
    monitor: object, *, obj_id: str, obj_kind: str, location: str, target_var: str
) -> None:
    callback = _callback(monitor, "record_object_allocated")
    if not callable(callback):
        raise TypeError("Debug monitor hook is not callable")
    _ignored: object = callback(
        obj_id=obj_id, obj_kind=obj_kind, location=location, target_var=target_var
    )


def record_call_constraint_processed(
    monitor: object, *, call_site: str, callee_var: str, callee_pts_size: int
) -> None:
    callback = _callback(monitor, "record_call_constraint_processed")
    if not callable(callback):
        raise TypeError("Debug monitor hook is not callable")
    _ignored: object = callback(
        call_site=call_site, callee_var=callee_var, callee_pts_size=callee_pts_size
    )


def record_call_failed(monitor: object, *, call_site: str, reason: str, details: str) -> None:
    callback = _callback(monitor, "record_call_failed")
    if not callable(callback):
        raise TypeError("Debug monitor hook is not callable")
    _ignored: object = callback(call_site=call_site, reason=reason, details=details)


def record_points_to_update(
    monitor: object, *, variable_str: str, old_size: int, new_size: int, added_objects: list[str]
) -> None:
    callback = _callback(monitor, "record_points_to_update")
    if not callable(callback):
        raise TypeError("Debug monitor hook is not callable")
    _ignored: object = callback(
        variable_str=variable_str, old_size=old_size, new_size=new_size, added_objects=added_objects
    )


def record_pfg_edge_activated(monitor: object, edge_id: str, num_objects: int) -> None:
    callback = _callback(monitor, "record_pfg_edge_activated")
    if not callable(callback):
        raise TypeError("Debug monitor hook is not callable")
    _ignored: object = callback(edge_id, num_objects)


def record_call_edge_created(
    monitor: object,
    *,
    caller: str,
    callee: str,
    call_site: str,
    callee_type: Literal["method", "function"],
) -> None:
    callback = _callback(monitor, "record_call_edge_created")
    if not callable(callback):
        raise TypeError("Debug monitor hook is not callable")
    _ignored: object = callback(
        caller=caller, callee=callee, call_site=call_site, callee_type=callee_type
    )


class _MissingMonitor(Enum):
    VALUE = "missing_monitor"


def load_optional_monitor(
    *,
    output_dir: str,
    log_interval: int,
    track_events: bool,
    track_object_flow: bool,
    track_pfg: bool,
    enabled: bool,
) -> object:
    """Load the actual optional analyzer component only when requested."""
    message = "Debug monitoring was requested, but the optional debug_monitor module is unavailable"
    try:
        module = importlib.import_module("pythonstan.analysis.pointer.kcfa.debug_monitor")
    except ImportError as exc:
        raise RuntimeError(message) from exc
    constructor: object = getattr(module, "DebugMonitor", _MissingMonitor.VALUE)
    if constructor is _MissingMonitor.VALUE:
        raise RuntimeError(message) from ImportError("cannot import name 'DebugMonitor'")
    if not callable(constructor):
        raise TypeError("DebugMonitor is not callable")
    monitor: object = constructor(
        output_dir=output_dir,
        log_interval=log_interval,
        track_events=track_events,
        track_object_flow=track_object_flow,
        track_pfg=track_pfg,
        enabled=enabled,
    )
    return monitor


def export_debug_data(
    monitor: object,
    state: "PointerAnalysisState",
    graph: "PointerFlowGraph",
    json_filename: str,
    report_filename: str,
) -> None:
    """Invoke the four existing export hooks in their original order."""
    points = _callback(monitor, "compute_points_to_statistics")
    if not callable(points):
        raise TypeError("Debug points statistics hook is not callable")
    _ignored_points: object = points(state)
    pfg = _callback(monitor, "compute_pfg_statistics")
    if not callable(pfg):
        raise TypeError("Debug PFG statistics hook is not callable")
    _ignored_pfg: object = pfg(graph)
    export = _callback(monitor, "export_to_json")
    if not callable(export):
        raise TypeError("Debug JSON export hook is not callable")
    _ignored_export: object = export(json_filename)
    report = _callback(monitor, "generate_summary_report")
    if not callable(report):
        raise TypeError("Debug report hook is not callable")
    _ignored_report: object = report(report_filename)
