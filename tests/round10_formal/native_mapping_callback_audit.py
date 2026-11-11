"""Reproduce an open native-key callback boundary without synthetic contexts.

This is an observation tool, not an expected-failure acceptance test. It never
asserts that the missing edges should remain missing. A future repair can make
the same source-bound witness report complete local dispatch coverage.
"""
import argparse
from collections import Counter
import contextlib
import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.pointer.kcfa.test_ai_bridge import analyze_source
from pythonstan.analysis.project.analysis import _implementation_hash


SOURCE = '''
observed = []
def hash_callback():
    observed.append("hash_callback")
    return 1
def equality_callback():
    observed.append("equality_callback")
    return True
class Key:
    def __hash__(self): return hash_callback()
    def __eq__(self, other): return equality_callback()
stored = Key()
lookup = Key()
mapping = {stored: "stored"}
missing = mapping.get(lookup)
inserted = mapping.setdefault(lookup)
'''


def audit():
    # Only this fixed, isolated language witness is executed. Neither imports
    # nor runtime traces from any upstream project enter the analyzer.
    concrete = {}
    exec(SOURCE, concrete)
    with TemporaryDirectory(prefix="pythonstan-native-key-audit-") as directory:
        with contextlib.redirect_stdout(io.StringIO()):
            query, edges = analyze_source(Path(directory), SOURCE,
                enable_synthetic_method_contexts=False, context_policy="2-cfa",
                index_sensitive=True, enable_ai_intrinsics=False)
        required_dispatch = {("subject", "subject.Key.__hash__"),
                             ("subject", "subject.Key.__eq__")}
        required_internal = {("subject.Key.__hash__", "subject.hash_callback"),
                             ("subject.Key.__eq__", "subject.equality_callback")}
        missing_dispatch = required_dispatch - edges
        missing_internal = required_internal - edges
        return {
            "schema_version": 1,
            "unit": "isolated_native_mapping_key_callback_dispatch",
            "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
            "implementation_sha256": _implementation_hash(),
            "source": SOURCE,
            "analysis_configuration": {"enable_synthetic_method_contexts": False,
                "context_policy": "2-cfa", "index_sensitive": True,
                "enable_ai_intrinsics": False, "max_iterations": 50000},
            "solver_converged": query.get_statistics()["converged"] == 1,
            "required_native_dispatch_edges": sorted(required_dispatch),
            "observed_native_dispatch_edges": sorted(required_dispatch & edges),
            "missing_native_dispatch_edges": sorted(missing_dispatch),
            "observed_internal_callback_edges": sorted(required_internal & edges),
            "missing_internal_callback_edges": sorted(missing_internal),
            "all_observed_edges": sorted(edges),
            "residual_effects": query.get_residual_effects(),
            "concrete_isolated_observation": {
                "callback_counts": dict(Counter(concrete["observed"])),
                "missing_result": concrete["missing"],
                "inserted_result": concrete["inserted"],
            },
            "isolated_synthetic_source_executed": True,
            "upstream_target_source_executed": False,
            "local_required_callback_edges_recovered": not missing_dispatch and not missing_internal,
            "whole_program_soundness_proved": False,
            "interpretation": "Convergence and synthetic method body activation cannot replace native-operation-to-special-method dispatch. This witness measures a bounded open obligation and does not certify all hashing/equality behavior.",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit()
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized)
    else:
        print(serialized, end="")
