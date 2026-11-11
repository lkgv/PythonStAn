#!/usr/bin/env python3
"""Check frozen hardcase effects in a separate finite closed heap model.

This command never executes target source, reads runtime outcomes, invokes an
LLM, changes gold, or changes KCFA results. Expected manifest values are untrusted
claims: source transfer derives final values before exact comparison.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pythonstan.analysis.pointer.ai.certified import closed_heap_effects as model
from pythonstan.analysis.pointer.ai.certified.heap_coroutines import entry_binding


def run(manifests, *, budget=None):
    rows = []
    identifiers = set()
    for path in manifests:
        path = Path(path)
        manifest_bytes = path.read_bytes()
        data = json.loads(manifest_bytes)
        for case in data["cases"]:
            if case["id"] in identifiers:
                raise ValueError("duplicate case id: " + case["id"])
            identifiers.add(case["id"])
            source_path = (path.parent / case["source"]).resolve()
            if not source_path.is_relative_to(path.parent.resolve()):
                raise ValueError("source path escapes cohort")
            source_bytes = source_path.read_bytes()
            if hashlib.sha256(source_bytes).hexdigest() != case["sha256"]:
                raise ValueError("source integrity failure: " + case["id"])
            effects = case["effects"]
            claims = {effect["runtime_global"]: effect["expected"] for effect in effects}
            if len(claims) != len(effects):
                raise ValueError("duplicate global projection in " + case["id"])
            await_globals = [case["await_global"]] if case.get("await_global") else []
            entry_spec, entry_digest = entry_binding(await_globals)
            candidate = {
                "source_sha256": case["sha256"],
                "semantics_version": model.SEMANTICS_VERSION,
                "globals": claims,
            }
            if await_globals:
                candidate["entry_sha256"] = entry_digest
            result = model.check_closed_effects(source_bytes.decode(), candidate,
                                                budget=budget, await_globals=await_globals)
            rows.append({
                "id": case["id"], "cohort": path.parent.name,
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "source_integrity_verified": True,
                "boundary_models": case.get("boundary_models", []),
                "entry_contract": entry_spec,
                "entry_sha256": entry_digest,
                "entry_origin": "frozen manifest await_global" if await_globals else "source module completion",
                "whole_application": False,
                "effect_obligations": len(effects),
                "effects": [{"id": effect["id"], "global": effect["runtime_global"],
                             "status": "conditional_verified" if result["status"] == "conditional_verified" else "residual",
                             "obligation": effect["obligation"]} for effect in effects],
                "closed_model": result,
            })
    total = sum(row["effect_obligations"] for row in rows)
    checked = sum(row["closed_model"]["conditional_effects_verified"] for row in rows)
    checker_files = [Path(model.__file__),
                     Path(model.__file__).with_name("heap_continuations.py"),
                     Path(model.__file__).with_name("heap_coroutines.py")]
    implementation = hashlib.sha256()
    file_hashes = {}
    for file in sorted(checker_files):
        content = file.read_bytes()
        name = str(file.relative_to(ROOT))
        file_hashes[name] = hashlib.sha256(content).hexdigest()
        implementation.update(name.encode() + b"\0" + content + b"\0")
    return {
        "schema_version": 1,
        "channel": "source_bound_closed_heap_conditional_effects",
        "semantics_version": model.SEMANTICS_VERSION,
        "checker_implementation_sha256": implementation.hexdigest(),
        "checker_files_sha256": file_hashes,
        "runtime_observations_used": False,
        "target_source_executed": False,
        "upstream_provenance_rechecked": False,
        "provenance_note": "Source bytes are checked against frozen manifests. The existing corpus runner separately checks raw upstream fragments and licenses.",
        "summary": {
            "cases": len(rows), "effect_obligations": total,
            "statuses": dict(Counter(row["closed_model"]["status"] for row in rows)),
            "conditional_effects_verified": checked,
            "conditional_effects_residual": total - checked,
            "effects_proved_for_unrestricted_python": 0,
            "effects_discharged_by_kcfa": 0,
            "global_soundness_established": False,
            "mechanized_semantics_proof": False,
        },
        "interpretation": [
            "Conditional checks cover only each exact closed source entry under the declared modeled semantics and boundary assumptions.",
            "The checker implementation is tested against independent Python witnesses; that differential validation is not a mechanized semantic correspondence proof.",
            "Unknown or unsupported source transitions retain their call prefix but discharge no effects and authorize no KCFA pruning.",
            "All original unrestricted soundness obligations remain open; conditional and runtime observations must never be added to static KCFA proof counts.",
        ],
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, action="append")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-steps", type=int, default=100_000)
    parser.add_argument("--max-call-depth", type=int, default=100)
    parser.add_argument("--max-objects", type=int, default=10_000)
    args = parser.parse_args()
    manifests = args.manifest or [ROOT / "benchmark/realworld_hardcases" / cohort / "manifest.json"
                                 for cohort in ("web_orm", "plugins", "validation")]
    try:
        report = run(manifests, budget=model.Budget(args.max_steps, args.max_call_depth, args.max_objects))
    except (ValueError, OSError, KeyError) as exc:
        parser.error(str(exc))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report["summary"], indent=2))
    else:
        print(json.dumps(report, indent=2))
    # Residual research obligations are reported rather than hidden as failures.
    # A completed mismatch is a model/oracle investigation that must fail CI.
    return 2 if any(row["closed_model"]["status"] in {"rejected", "model_exception"} for row in report["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
