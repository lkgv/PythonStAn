"""Observe native-key descriptor dispatch without freezing a missing-edge gold.

This is an observation tool. It reports what remains unproved and will accept
a future implementation recovering all required dispatches. Only the fixed,
isolated language witness is concretely executed; no upstream package runs.
"""
import argparse
from collections import Counter
import contextlib
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pythonstan.analysis.project.analysis import _implementation_hash
from tests.pointer.kcfa.test_ai_bridge import analyze_source


SOURCE = '''observed = []
def descriptor_callback():
    observed.append("descriptor")
def hash_callback():
    observed.append("hash")
    return 1
class HashDescriptor:
    def __get__(self, instance, owner):
        descriptor_callback()
        return hash_callback
class Key:
    __hash__ = HashDescriptor()
mapping = {}
result = mapping.get(Key())
'''


def audit():
    concrete = {}; exec(SOURCE, concrete)
    with TemporaryDirectory(prefix="pythonstan-descriptor-native-audit-") as temporary:
        with contextlib.redirect_stdout(io.StringIO()):
            query, edges = analyze_source(Path(temporary), SOURCE,
                enable_synthetic_method_contexts=False, work_scheduling_policy="fair",
                context_policy="2-cfa", index_sensitive=True, enable_ai_intrinsics=False)
        required_dispatch = {("subject", "subject.HashDescriptor.__get__"),
                             ("subject", "subject.hash_callback")}
        required_internal = {("subject.HashDescriptor.__get__", "subject.descriptor_callback")}
        return {
            "schema_version": 1, "unit": "native_mapping_custom_descriptor_callback",
            "implementation_sha256": _implementation_hash(),
            "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(), "source": SOURCE,
            "synthetic_method_contexts": False, "work_scheduling_policy": "fair",
            "solver_converged": query.get_statistics()["converged"] == 1,
            "concrete_callback_counts": dict(Counter(concrete["observed"])),
            "required_dispatch_edges": sorted(required_dispatch),
            "observed_dispatch_edges": sorted(required_dispatch & edges),
            "missing_dispatch_edges": sorted(required_dispatch - edges),
            "required_internal_edges": sorted(required_internal),
            "observed_internal_edges": sorted(required_internal & edges),
            "all_edges": sorted(edges), "residual_effects": query.get_residual_effects(),
            "bounded_dispatch_obligation_satisfied": (required_dispatch | required_internal) <= edges,
            "isolated_synthetic_source_executed": True, "upstream_source_executed": False,
            "whole_program_soundness_proved": False,
            "interpretation": "Ordinary source-method candidates and explicit descriptor residuals do not prove custom-descriptor lookup/invocation. This observation never requires the implementation to keep missing edges.",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(audit(), indent=2, sort_keys=True) + "\n")
