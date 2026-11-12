"""Run-local initial scalar input publication through the real Native engine.

Native points-to objects do not encode sort-restricted Top. Such inputs widen to
UnknownValue and retain an explicit projection obligation, never one sample.
"""

from copy import deepcopy
from collections.abc import Mapping
import math
from .allocation import canonical_constant
from .object import AllocSite, AllocKind, UnknownValue
from .points_to_set import PointsToSet
from .variable import Variable, VariableKind
from .pointer_flow_graph import NormalNode


def alternatives(descriptor):
    if isinstance(descriptor, Mapping):
        if "constants" in descriptor:
            values = descriptor["constants"]
            if not isinstance(values, (list, tuple)) or not values:
                raise ValueError("Input constants require a nonempty finite sequence")
            return tuple(values), None
        if "value" in descriptor:
            return (descriptor["value"],), None
        if descriptor.get("type") == "none":
            return (None,), None
        if descriptor.get("top") is True or descriptor.get("type") in {
            "str",
            "int",
            "bool",
            "float",
        }:
            return (), descriptor.get("type", "any")
        raise ValueError("Unsupported initial input descriptor")
    return (descriptor,), None


def validate_input_bindings(bindings):
    if not isinstance(bindings, Mapping):
        raise TypeError("Input bindings must be a mapping")
    result = deepcopy(dict(bindings))
    for name, descriptor in result.items():
        if (
            type(name) is not str
            or not name.rpartition(".")[0]
            or not name.rpartition(".")[2].isidentifier()
        ):
            raise ValueError("Initial input must identify a module global")
        values, top = alternatives(descriptor)
        for value in values:
            if (
                type(value) not in {str, int, bool, float, type(None)}
                or type(value) is float
                and not math.isfinite(value)
            ):
                raise ValueError("Native initial bindings require finite scalar inputs")
    return result


def seed_module_input_bindings(solver, scope, context):
    state = solver.state
    if scope in state._input_seeded:
        return
    state._input_seeded.add(scope)
    module = scope.stmt.get_qualname()
    for name, descriptor in state.input_bindings.items():
        owner, _, symbol = name.rpartition(".")
        if owner != module:
            continue
        values, top = alternatives(descriptor)
        published = list(values) if top is None else [None]
        for ordinal, value in enumerate(published):
            if not solver.work_ledger.consume(kind="input_publication"):
                state._residual_effects[(scope, "input_budget", symbol)] = {
                    "kind": "native_input_budget",
                    "origin": name,
                    "reason": "initial input publication budget exhausted",
                    "effects": ["pending_initial_input"],
                }
                break
            site = AllocSite(
                ("initial_input", module, symbol, ordinal),
                AllocKind.UNKNOWN if top else AllocKind.CONSTANT,
            )
            if top is not None:
                obj = UnknownValue(state.select_allocation_context(context, site), site)
                state._heap.set_obj(scope, context, site, obj)
                state.obj_scope[obj] = scope
                state._residual_effects[(scope, "input_projection", symbol)] = {
                    "kind": "native_input_projection",
                    "origin": name,
                    "sort": top,
                    "reason": "Native scalar objects do not represent sort-restricted Top; widened to full unknown",
                    "effects": ["unknown_initial_value"],
                }
            else:
                obj = canonical_constant(state, context, site, value)
            variable = state.get_variable(scope, context, Variable(symbol, VariableKind.GLOBAL))
            # Publish the initial fact through the actual Native transfer, not
            # an unprocessed queue item. Source consumers are registered later
            # and replay this already published input through normal edges.
            solver._apply_dynamic(scope, NormalNode(variable), PointsToSet.singleton(obj))
            solver._stats["input_publications"] = solver._stats.get("input_publications", 0) + 1
