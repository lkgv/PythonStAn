#!/usr/bin/env python3
"""Derive or check source-bound finite-entry relations without target execution.

The default witness is a development integration fixture: eight combinations of
registry override, handled failure and route name. It is not an upstream web
application or an empirical sample independent of development.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pythonstan.analysis.pointer.ai.certified.finite_entry import (
    FamilyBudget, FiniteEntrySpec, analyze_finite_entry, check_finite_entry,
)
from pythonstan.analysis.pointer.ai.certified.closed_heap_effects import Budget


def read_spec(path):
    data = json.loads(Path(path).read_text())
    required = {"entry_function", "positional_domains", "keyword_domains", "observed_globals"}
    if type(data) is not dict or set(data) != required:
        raise ValueError("spec requires entry_function, positional_domains, keyword_domains, observed_globals")
    if any(type(data[key]) is not list for key in required - {"entry_function"}):
        raise ValueError("domain and observation collections must be JSON arrays")
    def immutable(value):
        # Within one domain alternative a JSON array explicitly denotes an
        # immutable tuple. Objects and user-defined values are not admitted.
        return tuple(immutable(v) for v in value) if type(value) is list else value
    positional = []
    for values in data["positional_domains"]:
        if type(values) is not list:
            raise ValueError("each positional domain must be an array")
        positional.append(tuple(immutable(v) for v in values))
    keywords = []
    for item in data["keyword_domains"]:
        if type(item) is not list or len(item) != 2 or type(item[1]) is not list:
            raise ValueError("each keyword domain must be [name, alternatives]")
        keywords.append((item[0], tuple(immutable(v) for v in item[1])))
    return FiniteEntrySpec(data["entry_function"], tuple(positional), tuple(keywords), tuple(data["observed_globals"]))


def main():
    fixture = ROOT / "benchmark/round6_composition"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=fixture / "registry_entry.py")
    parser.add_argument("--spec", type=Path, default=fixture / "entry_spec.json")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--derive-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-variants", type=int, default=256)
    parser.add_argument("--max-steps", type=int, default=100_000)
    parser.add_argument("--max-call-depth", type=int, default=100)
    parser.add_argument("--max-objects", type=int, default=10_000)
    args = parser.parse_args()
    try:
        budget = FamilyBudget(max_variants=args.max_variants,
                              per_variant=Budget(args.max_steps,args.max_call_depth,args.max_objects))
        source = args.source.read_text()
        spec = read_spec(args.spec)
        if args.derive_only:
            if args.candidate is not None:
                raise ValueError("derive-only cannot accept a candidate")
            result = analyze_finite_entry(source, spec, budget=budget)
        else:
            candidate_path = args.candidate or fixture / "candidate.json"
            result = check_finite_entry(source, spec, json.loads(candidate_path.read_text()), budget=budget)
    except (ValueError, OSError, TypeError) as exc:
        parser.error(str(exc))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({key:result[key] for key in ("status", "declared_variants", "covered_variants", "verified_variants", "source_sha256", "implementation_sha256")},indent=2))
    else:
        print(json.dumps(result, indent=2))
    return 0 if result["status"] in {"finite_model_completed", "conditional_family_verified"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
