#!/usr/bin/env python3
"""Record baseline/current test obligations without treating ID counts as proof.

This is a read-only local Git comparison; it performs no network operations.
Hypothesis rule methods and conditional pytest candidates are identified
separately. A retained name or assertion count does not prove equal coverage.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess


DEFAULT_BASE = "d9cc0e6108b490135ef57b5597bfd3962c3e4d15"


def git(root, *args, required=True):
    result = subprocess.run(["git", "-C", str(root), *args], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if required and result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout if result.returncode == 0 else None


def inventory(source):
    records = {}

    def walk(nodes, classes=(), conditions=()):
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                walk(node.body, (*classes, node.name), conditions)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("test_"):
                    continue
                decorators = [ast.unparse(item) for item in node.decorator_list]
                is_rule = any(text == "rule" or text.startswith("rule(")
                              or text.startswith("hypothesis.stateful.rule(")
                              for text in decorators)
                kind = "hypothesis_rule" if is_rule else (
                    "pytest_candidate" if not classes or classes[-1].startswith("Test")
                    else "helper_test_named")
                records["::".join((*classes, node.name))] = {
                    "collection_kind": kind,
                    "conditional_blocks": list(conditions),
                    "line": node.lineno,
                    "assertions": [ast.unparse(item) for item in ast.walk(node)
                                   if isinstance(item, ast.Assert)],
                    "exception_expectations": [ast.unparse(item) for item in ast.walk(node)
                                               if isinstance(item, ast.Call)
                                               and ast.unparse(item.func) in ("pytest.raises", "self.assertRaises")],
                    "decorators": decorators,
                    "skip_calls": [ast.unparse(item) for item in ast.walk(node)
                                   if isinstance(item, ast.Call)
                                   and ast.unparse(item.func) in ("pytest.skip", "pytest.xfail", "self.skipTest")],
                    "body_sha256": hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest(),
                }
            elif isinstance(node, ast.If):
                condition = ast.unparse(node.test)
                walk(node.body, classes, (*conditions, condition))
                walk(node.orelse, classes, (*conditions, "not (" + condition + ")"))
            else:
                for name in ("body", "orelse", "finalbody"):
                    body = getattr(node, name, ())
                    if isinstance(body, list):
                        walk(body, classes, conditions)
                for handler in getattr(node, "handlers", ()):
                    walk(handler.body, classes, conditions)

    walk(ast.parse(source).body)
    return records


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest() if text is not None else None


def file_gates(source):
    """Include module/class/fixture gates, not only test-body skip calls."""
    if source is None:
        return []
    names = {"pytest.skip", "pytest.xfail", "pytest.importorskip", "self.skipTest",
             "pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail",
             "unittest.skip", "unittest.skipIf", "unittest.skipUnless", "unittest.expectedFailure"}
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and ast.unparse(node.func) in names:
            found.append({"line": node.lineno, "expression": ast.unparse(node)})
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets):
                found.append({"line": node.lineno, "expression": ast.unparse(node)})
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) and ast.unparse(decorator) in names:
                    found.append({"line": decorator.lineno, "expression": ast.unparse(decorator)})
    return sorted(found, key=lambda item: (item["line"], item["expression"]))


def run(root, baseline, phase):
    baseline = git(root, "rev-parse", baseline).strip()
    changed = set(git(root, "diff", "--name-only", baseline, "--", "tests").splitlines())
    changed.update(git(root, "ls-files", "--others", "--exclude-standard", "--", "tests").splitlines())
    records = []
    for filename in sorted(changed):
        if not filename.endswith(".py"):
            continue
        before_source = git(root, "show", baseline + ":" + filename, required=False)
        current = root / filename
        after_source = current.read_text() if current.exists() else None
        before = inventory(before_source) if before_source is not None else {}
        after = inventory(after_source) if after_source is not None else {}
        records.append({
            "file": filename,
            "baseline_source_sha256": digest(before_source),
            "current_source_sha256": digest(after_source),
            "before_file_gates": file_gates(before_source),
            "after_file_gates": file_gates(after_source),
            "before_count": len(before), "after_count": len(after),
            "removed_ids": sorted(set(before) - set(after)),
            "added_ids": sorted(set(after) - set(before)),
            "before": before, "after": after,
        })
    kinds = ("pytest_candidate", "hypothesis_rule", "helper_test_named")
    summary = {side: {kind: sum(record["collection_kind"] == kind
                                for file in records for record in file[side].values())
                      for kind in kinds} for side in ("before", "after")}
    return {
        "base_commit": baseline, "phase": phase,
        "kind": "structural migration evidence; counts do not prove semantic equivalence",
        "collection_note": "pytest_candidate includes all conditional definitions before runtime collection; parameter expansion, skips and Hypothesis rules are not pytest case counts",
        "reproduce": "python scripts/audit_test_migrations.py --baseline " + baseline + " --phase " + phase,
        "summary": summary, "files": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=DEFAULT_BASE)
    parser.add_argument("--phase", choices=("integration", "final"), default="integration")
    parser.add_argument("--output", type=Path,
                        default=Path("docs/certified_analysis/evidence/round5_test_migration_audit.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = run(root, args.baseline, args.phase)
    output = args.output if args.output.is_absolute() else root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"output": str(output), "phase": args.phase,
                      "files": len(result["files"]), "definition_summary": result["summary"]}))


if __name__ == "__main__":
    main()
