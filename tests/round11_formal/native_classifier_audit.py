"""Audit scalar/custom key arrival after a completed production prefix.

Only the final method-dispatch body is replaced by an observer. Production
metadata flow, classification, observation replay and queue handling run.
Actual source method dispatch/effects are separately tested by pytest.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path

from tests.round11_formal.test_scheduler_event_boundaries import model
from pythonstan.analysis.pointer.kcfa.object import ConstantObject, DictObject, AllocSite, AllocKind
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.native_mapping import (
    NativeMappingProtocols, NativeMappingProtocolConstraint, NATIVE_MAPPING_KEYS,
)
from pythonstan.analysis.pointer.kcfa.context import CallSite
from pythonstan.analysis.pointer.kcfa.variable import Variable
from pythonstan.analysis.project.analysis import _implementation_hash
from pythonstan.ir import IRCall


def audit():
    rows = []
    for policy in ("legacy", "fair"):
        for direction in ("late_custom_stored", "late_custom_query"):
            reset_object_table()
            solver, state, scope, context, variable, value = model(policy)
            mapping = DictObject(context, AllocSite("controlled-mapping", AllocKind.DICT))
            solver.handle_new_points_to(variable("mapping"), scope, PointsToSet.singleton(mapping))
            solver.solve_to_fixpoint()
            manager = NativeMappingProtocols(solver)
            captured = []
            manager._method = lambda owner, constraint, receiver, method, others=(): captured.append(
                (receiver, method, tuple(others)))
            site = CallSite(IRCall(ast.parse("native_get(query)").body[0].value), "formal_native_classifier")
            constraint = NativeMappingProtocolConstraint(Variable("mapping"), Variable("query"), site)
            manager.apply(scope, constraint)
            scalar = ConstantObject(context, AllocSite("scalar", AllocKind.CONSTANT), "scalar")
            custom = value("custom key")
            stored = state.get_field(scope, context, mapping, NATIVE_MAPPING_KEYS)
            before = variable("query") if direction == "late_custom_stored" else stored
            after = stored if direction == "late_custom_stored" else variable("query")
            solver.handle_new_points_to(before, scope, PointsToSet.singleton(scalar))
            solver.solve_to_fixpoint()
            assert solver._stats["converged"] == 1
            assert not any(receiver == custom for receiver, _, _ in captured)
            solver.handle_new_points_to(after, scope, PointsToSet.singleton(custom))
            solver.solve_to_fixpoint()
            assert solver._stats["converged"] == 1
            assert (custom, "__eq__", (scalar,)) in captured
            rows.append({"policy": policy, "direction": direction,
                         "completed_before_late_arrival": True,
                         "custom_equality_candidate_retained": True,
                         "statistics": dict(manager.statistics)})
    return {"schema_version": 1, "implementation_sha256": _implementation_hash(),
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "rows": rows, "all_four_late_arrivals_retained": True,
            "scope": "Production metadata classification/subscription relation, final method-dispatch body replaced by observer; actual source dispatch/effects tested separately.",
            "source_executed": False, "whole_program_soundness_proved": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit(), indent=2) + "\n")
