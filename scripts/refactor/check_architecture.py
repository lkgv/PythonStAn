#!/usr/bin/env python3
"""Check static PythonStAn layering, including function-local imports.

Audit mode preserves migration debt as findings. Release mode enforces both
new and legacy boundaries; no allowlist can turn a historical violation into
a release success.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def module_name(path: Path, root: Path) -> str:
    name = ".".join(path.relative_to(root).with_suffix("").parts)
    return name.removesuffix(".__init__")


def imported_modules(tree: ast.AST, module: str, package: bool):
    """Resolve relative import roots and members without importing target code."""
    base = module if package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = base.split(".")
                parts = parts[:len(parts) - node.level + 1]
                target = ".".join(parts + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            yield node.lineno, target
            for alias in node.names:
                if alias.name != "*":
                    yield node.lineno, target + "." + alias.name
        elif isinstance(node, ast.Call):
            call_name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if call_name in {"__import__", "import_module"} and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                yield node.lineno, node.args[0].value


def layer(module: str) -> str:
    if module.startswith("pythonstan.analysis.engines.andersen"):
        return "andersen"
    if module.startswith("pythonstan.analysis.engines.abstract"):
        return "abstract"
    if module.startswith("pythonstan.analysis.pointer.kcfa"):
        return "legacy_andersen"
    if module.startswith("pythonstan.analysis.pointer.ai.certified"):
        return "legacy_verification"
    if module.startswith("pythonstan.analysis.pointer.ai"):
        return "legacy_abstract"
    for name in ("contracts", "domains", "semantics", "composition", "policies", "verification", "clients"):
        if module.startswith("pythonstan.analysis." + name):
            return name
    if module.startswith("pythonstan.frontend"):
        return "frontend"
    if module.startswith("pythonstan.compat"):
        return "compat"
    if module.startswith("pythonstan_inspector"):
        return "inspector"
    if module.startswith("pythonstan.llm"):
        return "llm"
    return "other"


def violations(source: str, target: str):
    a, b = layer(source), layer(target)
    core = {"contracts", "domains", "semantics", "policies", "andersen", "abstract", "frontend"}
    if a in core and b in {"inspector", "llm"}:
        yield "core-must-not-depend-on-optional-consumer"
    if a == "contracts" and b in {"domains", "andersen", "abstract", "legacy_andersen", "legacy_abstract"}:
        yield "contracts-must-be-engine-neutral"
    if a == "domains" and b in {"andersen", "abstract", "legacy_andersen", "legacy_abstract", "compat"}:
        yield "domains-must-not-depend-on-engine"
    if a in {"andersen", "abstract"} and b in {"andersen", "abstract"} and a != b:
        yield "engines-bridge-only-through-composition"
    if a in {"legacy_andersen", "legacy_abstract"} and b in {"legacy_andersen", "legacy_abstract"} and a != b:
        yield "legacy-engine-bridge-debt"
    if a in core and target.startswith("pythonstan.world"):
        yield "new-core-must-not-use-world-singleton"
    if a in core and target.startswith("pythonstan.analysis.pointer"):
        yield "new-core-must-not-call-old-engine-path"
    if source.startswith("pythonstan") and not source.startswith("pythonstan_inspector") and b == "inspector":
        yield "inspector-must-remain-external"
    if target in {"pythonstan.analysis.scope", "pythonstan.analysis.scope.closure", "pythonstan.analysis.class_hierarchy"}:
        yield "duplicate-closure-source"


def inspect_root(root: Path):
    findings = []
    closure_definitions = []
    for package in ("pythonstan", "pythonstan_inspector"):
        folder = root / package
        for path in sorted(folder.rglob("*.py")):
            source = module_name(path, root)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == "ClosureAnalysis":
                    closure_definitions.append((source, path, node.lineno))
            seen = set()
            for line, target in imported_modules(tree, source, path.name == "__init__.py"):
                for rule in violations(source, target):
                    key = (line, target, rule)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append({"source": source, "target": target,
                                     "path": str(path.relative_to(root)),
                                     "line": line, "rule": rule})
    if len(closure_definitions) > 1:
        for source, path, line in closure_definitions:
            findings.append({"source":source,"target":"ClosureAnalysis","path":str(path.relative_to(root)),"line":line,"rule":"duplicate-closure-implementation"})
    for path in sorted((root / "scripts").glob("*.py")):
        source = "scripts." + path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, target in imported_modules(tree, source, False):
            if target in {"pythonstan.analysis.scope", "pythonstan.analysis.scope.closure"}:
                findings.append({"source":source,"target":target,"path":str(path.relative_to(root)),"line":line,"rule":"duplicate-closure-source"})
    return findings


def self_test():
    cases = [
        ("pythonstan.analysis.engines.abstract.driver", "pythonstan.analysis.engines.andersen.solver", "engines-bridge-only-through-composition"),
        ("pythonstan.analysis.domains.values", "pythonstan.analysis.engines.abstract.driver", "domains-must-not-depend-on-engine"),
        ("pythonstan.analysis.contracts.results", "pythonstan.analysis.domains.values", "contracts-must-be-engine-neutral"),
        ("pythonstan.analysis.engines.abstract.driver", "pythonstan_inspector.runner", "core-must-not-depend-on-optional-consumer"),
        ("scripts.do_closure", "pythonstan.analysis.scope", "duplicate-closure-source"),
    ]
    for source, target, expected in cases:
        assert expected in set(violations(source, target)), (source, target)
    assert not list(violations("pythonstan.analysis.composition.oracle", "pythonstan.analysis.engines.abstract.driver"))
    tree = ast.parse("def f():\n    from ..andersen import solver\n")
    found = set(imported_modules(tree, "pythonstan.analysis.engines.abstract.driver", False))
    assert (2, "pythonstan.analysis.engines.andersen") in found
    dynamic = ast.parse("def f():\n    importlib.import_module('pythonstan_inspector.runner')\n")
    assert (2, "pythonstan_inspector.runner") in set(imported_modules(dynamic, "pythonstan.analysis.engines.abstract.driver", False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
    findings = inspect_root(args.root.resolve())
    enforced = findings if args.release else [x for x in findings if not x["rule"].startswith(("legacy-", "duplicate-closure-"))]
    report = {"schema_version": 1, "mode": "release" if args.release else "migration", "findings": findings, "enforced_violations": len(enforced)}
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if enforced else 0


if __name__ == "__main__":
    raise SystemExit(main())
