"""Lower an entire pinned Django file; inspect distinct original wrapper bodies.

This is a full-source-file frontend witness, not a whole-project solver result
or a proof of runtime descriptor/lazy proxy behavior. No target is executed.
"""
import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from benchmark.round8_corpus.registry import projects
from tests.analysis.legacy_ai_support import isolated_world
from tests.round10_applications.test_framework_closure_reductions import lower, closure


def run(cache, output):
    record = next(p for p in projects() if p["id"] == "django")
    source = "django/utils/functional.py"
    inventory = next(p for p in record["source_files"] if p["path"] == source)
    path = Path(cache) / record["project_root"] / source
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != inventory["sha256"]:
        raise ValueError("Pinned full source hash mismatch")
    with TemporaryDirectory() as directory, isolated_world():
        world, modules = lower(Path(directory), {"django.utils.functional": data.decode()})
        analysis = closure()
        analysis.analyze_module(modules[0])
        wrappers = sorted((s for s in world.scope_manager.get_scopes()
                           if s.get_qualname() == "django.utils.functional.lazy.__wrapper__"),
                          key=lambda s: s.get_ast().lineno)
        if [s.get_ast().lineno for s in wrappers] != [184, 193]:
            raise ValueError("Unexpected pinned definition identities")
        if "func" not in wrappers[0].cell_vars or "__proxy__" not in wrappers[1].cell_vars:
            raise ValueError("Original wrapper capture lost")
        if world.scope_manager.get_ir(wrappers[0], "ir") is world.scope_manager.get_ir(wrappers[1], "ir"):
            raise ValueError("Original wrapper bodies alias")
        result = {"schema_version": 1, "project_id": "django", "version": record["version"],
            "unit": "one_full_source_file_frontend_scope_identity_witness",
            "source": source, "source_sha256": inventory["sha256"],
            "source_bytes": len(data), "translated_scopes": len(world.scope_manager.get_scopes()),
            "wrappers": [{"qualname": s.get_qualname(), "line": s.get_ast().lineno,
                          "closure_cells": sorted(s.cell_vars)} for s in wrappers],
            "distinct_canonical_bodies": True, "required_captures_present": True,
            "target_source_executed": False, "whole_project_analyzed": False,
            "runtime_dispatch_proved": False, "whole_program_soundness_proved": False}
    if hashlib.sha256(path.read_bytes()).hexdigest() != inventory["sha256"]:
        raise ValueError("Source changed during witness")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.cache, args.output)))
