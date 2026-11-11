"""Source/entry observation ledger; no runtime activation or safety proof."""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import tokenize

from benchmark.round8_corpus.registry import projects


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def modules_for(record):
    result = {}
    for file in record["source_files"]:
        path = Path(file["path"])
        for base in record["import_roots"]:
            try:
                relative = path.relative_to(Path(base))
            except ValueError:
                continue
            parts = list(relative.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            name = ".".join(parts)
            if name:
                result[name] = file
    return result


def definitions(source, module):
    found = {}
    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.scope = []

        def visit_ClassDef(self, node):
            self.scope.append((node.name, "class"))
            self.generic_visit(node)
            self.scope.pop()

        def visit_FunctionDef(self, node):
            parent = self.scope[-1][1] if self.scope else "module"
            name = ".".join([module, *[part[0] for part in self.scope], node.name])
            decorators = [ast.unparse(d) for d in node.decorator_list]
            kind = "function" if parent == "module" else "nested_function"
            if parent == "class":
                kind = ("classmethod_decorator_syntax" if "classmethod" in decorators else
                        "staticmethod_decorator_syntax" if "staticmethod" in decorators else
                        "instance_method_syntax")
            found.setdefault(name, []).append({"line": node.lineno, "end_line": node.end_lineno,
                "definition_kind": kind, "async": isinstance(node, ast.AsyncFunctionDef),
                "decorator_syntax": decorators, "parameter_names": [a.arg for a in
                    [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]],
                "vararg": node.args.vararg.arg if node.args.vararg else None,
                "kwarg": node.args.kwarg.arg if node.args.kwarg else None,
                "immediate_parent_kind": parent})
            self.scope.append((node.name, "function"))
            self.generic_visit(node)
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef
    Visitor().visit(ast.parse(source))
    return found


def nodes_with_owner(tree, module):
    """Visit lexical owners through control-flow containers, not dot lookup."""
    def walk(node, owner):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = owner + "." + node.name
        yield owner, node
        for child in ast.iter_child_nodes(node):
            yield from walk(child, owner)
    return walk(tree, module)


def audit(cache, evidence, output):
    rows = []
    report_bindings = []
    summary_path = Path(evidence) / "summary.json"
    summary = json.loads(summary_path.read_text())
    expected_projects = {p["id"] for p in projects()}
    trials = summary["rows"]
    if len(trials) != len(expected_projects) or {
            (r["project_id"], r["stage"], r["repeat"]) for r in trials} != {
            (p, "solver", 1) for p in expected_projects}:
        raise ValueError("Expected exactly one solver/repeat-1 raw trial per project")
    trial_rows = {r["project_id"]: r for r in trials}
    reports = {}
    for record in projects():
        root = Path(cache) / record["project_root"]
        module_files = modules_for(record)
        report_path = Path(evidence) / (record["id"] + "_solver_r1.json")
        raw = json.loads(report_path.read_text())
        if (raw["project_id"], raw["stage"], raw["repeat"]) != (record["id"], "solver", 1):
            raise ValueError("Raw project/stage/repeat identity mismatch")
        binding = trial_rows[record["id"]]
        if binding["path"] != report_path.name or binding["sha256"] != sha(report_path):
            raise ValueError("Raw report does not match trial summary binding")
        report = raw["report"]
        reports[record["id"]] = report
        report_bindings.append({"project_id": record["id"], "raw_report": report_path.name,
                                "sha256": sha(report_path)})
        parsed = {}
        activation = {a["qualified_name"]: a for a in report.get("exported_entry_activation", [])}
        scope_rows = report.get("scope_coverage", [])
        for entry in record["entry_points"]:
            owners = [m for m in module_files if entry.startswith(m + ".")]
            if not owners:
                raise ValueError("No source module for reviewed entry " + entry)
            module = max(owners, key=len)
            file = module_files[module]
            path = root / file["path"]
            if sha(path) != file["sha256"]:
                raise ValueError("Source hash mismatch")
            if module not in parsed:
                with tokenize.open(path) as stream:
                    parsed[module] = definitions(stream.read(), module)
            nodes = parsed[module].get(entry, [])
            scope = [a for a in scope_rows if a["name"] == entry]
            selected = report.get("source_scope_entry_assumptions")
            act = activation.get(entry)
            rows.append({"project_id": record["id"], "project_version": record["version"],
                "entry": entry, "source": file["path"], "source_sha256": file["sha256"],
                "source_definitions": nodes, "source_definition_unique": len(nodes) == 1,
                "report_status": report["status"],
                "resolved_entry_observed": (entry in report["resolved_entry_points"])
                    if "resolved_entry_points" in report else None,
                "source_scope_assumption_observed": entry in selected if selected is not None else None,
                "exported_activation": act,
                "scope_observations": scope,
                "entry_receiver_coverage_proved": False, "entry_argument_coverage_proved": False,
                "async_resume_coverage_proved": False, "runtime_dispatch_proved": False,
                "interpretation": "Source declaration and recorded scheduling/contexts are separate observations; neither establishes exhaustive external entry behavior."})
    result = {"schema_version": 1, "unit": "reviewed_entry_source_and_recorded_activation",
        "projects": len(report_bindings), "declared_entries": len(rows),
        "source_unique_entries": sum(r["source_definition_unique"] for r in rows),
        "syntax_distribution": dict(Counter(n["definition_kind"] for r in rows for n in r["source_definitions"])),
        "async_declarations": sum(n["async"] for r in rows for n in r["source_definitions"]),
        "installed_exported_bindings_observed": sum(bool(r["exported_activation"] and
            r["exported_activation"]["status"] == "exported_binding_call_installed") for r in rows),
        "source_scope_only_activation_observed": sum(bool(r["exported_activation"] and
            r["exported_activation"]["status"] == "source_scope_only") for r in rows),
        "summary_sha256": sha(summary_path), "report_bindings": report_bindings, "rows": rows,
        "entry_coverage_proved": False, "target_source_executed": False,
        "whole_program_soundness_proved": False,
        "interpretation": "All declarations are source/hash bound. Missing runtime observations are not zero contexts; zero recorded contexts are not proof of impossibility. Decorator classification is syntax only."}
    # The frozen objectives are reviewed questions. A source match and a
    # scheduled scope are observations, never a discharged relation/property.
    challenges = []
    anchor_count = 0
    manifests = []
    records = {p["id"]: p for p in projects()}
    for cohort in ("web", "ops", "ai"):
        manifest = Path(__file__).resolve().parents[2] / "benchmark" / "round8_security" / (cohort + "_challenges.json")
        manifests.append({"name": manifest.name, "sha256": sha(manifest)})
        for challenge in json.loads(manifest.read_text())["challenges"]:
            record = records[challenge["project_id"]]
            module_files = modules_for(record)
            source_modules = {f["path"]: m for m, f in module_files.items()}
            anchors = []
            for anchor in challenge["anchors"]:
                path = Path(cache) / record["project_root"] / anchor["source"]
                inventory_hash = next(f["sha256"] for f in record["source_files"] if f["path"] == anchor["source"])
                if sha(path) != anchor["sha256"] or anchor["sha256"] != inventory_hash:
                    raise ValueError("Challenge source hash mismatch")
                with tokenize.open(path) as stream:
                    tree = ast.parse(stream.read())
                # Check both function ownership and the exact expression/line.
                if not any(owner == anchor["qualname"] and getattr(node, "lineno", None) == anchor["line"]
                           and ast.unparse(node) == anchor["expression"]
                           for owner, node in nodes_with_owner(tree, source_modules[anchor["source"]])):
                    raise ValueError("Challenge AST ownership/expression/line mismatch: " + repr(anchor))
                observation = [s for s in reports[record["id"]].get("scope_coverage", []) if s["name"] == anchor["qualname"]]
                anchors.append({"id": anchor.get("id"), "source": anchor["source"],
                    "source_sha256": anchor["sha256"], "qualname": anchor["qualname"],
                    "line": anchor["line"], "expression": anchor["expression"],
                    "source_binding_verified": True,
                    "is_reviewed_entry": anchor["qualname"] in record["entry_points"],
                    "scope_observations": observation,
                    "relation_or_effect_proved": False})
            anchor_count += len(anchors)
            challenges.append({"id": challenge["id"], "project_id": record["id"],
                "required_properties": len(challenge["required_properties"]), "anchors": anchors,
                "relations": len(challenge["relations"]), "status": "unproved",
                "proof_obligations_discharged": False})
    if len(challenges) != len(expected_projects) or {c["project_id"] for c in challenges} != expected_projects:
        raise ValueError("Objectives must retain exactly one question per project")
    result["objective_audit"] = {"manifest_bindings": manifests, "challenges": len(challenges),
        "required_properties": sum(c["required_properties"] for c in challenges),
        "anchors": anchor_count, "rows": challenges, "proved_challenges": 0}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.cache, args.evidence, args.output)
    print(json.dumps({k: result[k] for k in ["projects", "declared_entries", "source_unique_entries",
        "syntax_distribution", "async_declarations", "installed_exported_bindings_observed", "source_scope_only_activation_observed"]}))
