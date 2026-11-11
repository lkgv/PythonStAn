"""Child-only legacy bridge. No live analysis object leaves this process."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import time
import traceback


def _names(source):
    class Names(ast.NodeVisitor):
        def __init__(self): self.names = set()
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Store): self.names.add(node.id)
        def visit_FunctionDef(self, node): self.names.add(node.name)
        visit_AsyncFunctionDef = visit_FunctionDef
        def visit_ClassDef(self, node): self.names.add(node.name)
        def visit_Import(self, node):
            self.names.update(n.asname or n.name.split(".")[0] for n in node.names)
        def visit_ImportFrom(self, node):
            self.names.update(n.asname or n.name for n in node.names if n.name != "*")
    visitor = Names(); visitor.visit(ast.parse(source)); return visitor.names


def _value(objects):
    from pythonstan.analysis.contracts import value_snapshot, thaw_snapshot
    constants, ids, types = [], [], set()
    top = False
    top_sorts = set()
    for obj in objects:
        kind = getattr(getattr(obj, "kind", None), "value", "")
        if kind == "unknown": top = True
        if hasattr(obj, "value") and type(obj.value) in (str, bool, int, float, type(None)):
            if type(obj.value) is float and not math.isfinite(obj.value):
                top_sorts.add("float"); types.add("float"); continue
            if not any(type(x) is type(obj.value) and x == obj.value for x in constants): constants.append(obj.value)
            types.add(type(obj.value).__name__)
        else:
            ids.append(hashlib.sha256(str(obj).encode()).hexdigest()[:24])
            types.add(str(kind))
    result = thaw_snapshot(value_snapshot(constants=sorted(constants, key=lambda x: (type(x).__name__, repr(x))),
                                         objects=sorted(set(ids)), types=sorted(types), top=top))
    if top_sorts:
        result["top_sorts"] = sorted(top_sorts); result["bottom"] = False
    return result


def main(payload, publish=None):
    config = payload["config"]
    if config["resources"]["max_work"] == 0:
        return {"engine": config["engine"], "solver_status": "budget_exhausted",
                "coverage_status": "open", "values": {}, "calls": [], "returns": {},
                "effects": {}, "outcomes": {}, "stats": {"native_work": 0},
                "obligations": [{"kind": "work_budget", "pending": True}],
                "phase_trace": [{"phase": "native", "work": 0, "status": "budget_exhausted"}]}
    # OS memory bound is child-only. No caller process/global limits change.
    memory = config["resources"]["max_memory_mb"]
    if memory:
        import resource
        cap = int(memory * 1024 * 1024)
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    from pythonstan.world.pipeline import Pipeline
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.analysis.contracts import SourceModule
    aggregate = {"engine": config["engine"], "solver_status": "converged", "coverage_status": "open",
                 "values": {}, "returns": {}, "outcomes": {}, "calls": [], "effects": {},
                 "obligations": [{"kind": "legacy_source_coverage", "reason": "Native constraint convergence does not establish exhaustive Python source coverage"}],
                 "diagnostics": [], "stats": {"scenarios": 0, "native_work": 0}, "phase_trace": []}
    modules = {m["module"]: m for m in payload["modules"]}
    source_snapshots = {name: SourceModule(**module) for name, module in modules.items()}
    with tempfile.TemporaryDirectory(prefix="pythonstan-legacy-") as directory:
        root = Path(directory)
        paths = {}
        for name, module in modules.items():
            path = root.joinpath(*name.split("."))
            path = path / "__init__.py" if module["is_package"] else path.with_suffix(".py")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(module["source"], encoding="utf-8"); paths[name] = path
        for scenario in payload["scenarios"]:
            remaining_work = config["resources"]["max_work"] - aggregate["stats"].get("global_work_used", 0)
            if remaining_work <= 0:
                aggregate["solver_status"] = "budget_exhausted"
                aggregate["obligations"].append({"kind": "work_budget", "stage": "native", "pending_scenarios": True})
                break
            native = dict(config["andersen"])
            native["entry_points"] = ([scenario["root_module"] + "." + scenario["entry_qualname"]]
                                      if scenario["entry_qualname"] else [])
            native["max_iterations"] = min(native["max_iterations"], remaining_work)
            native["project_path"] = str(root)
            # This isolated child owns logging; diagnostics never enter NDJSON.
            import logging
            logging.basicConfig(level=getattr(logging,native["log_level"]),stream=sys.stderr,
                                format="%(levelname)s: %(message)s",force=True)
            native = Config.from_dict(native).to_dict()
            analysis = [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"], "options": native}]
            started = time.monotonic()
            pipeline = Pipeline(config={"filename": str(paths[scenario["root_module"]]),
                                        "project_path": str(root), "library_paths": [], "import_level": -1,
                                        "mock_libs": False, "time_count": False, "analysis": analysis},
                                source_snapshots=source_snapshots)
            from pythonstan.compat.native_services import configure_native_services
            source_metadata = {name: {"__file__": module["filename"], "__name__": name,
                                     "__package__": name if module["is_package"] else name.rpartition(".")[0]}
                               for name, module in modules.items()}
            configure_native_services(pipeline.analysis_manager, max_work=remaining_work, source_metadata=source_metadata,
                                      input_bindings=config["inputs"]["bindings"])
            pipeline.run()
            native_result = pipeline.analysis_manager.get_results("pta")
            query = native_result.query()
            stats = query.get_statistics()
            native_seconds = time.monotonic()-started
            if native["enable_synthetic_method_contexts"]:
                contract=aggregate.setdefault("synthetic_entry_contract",{
                    "enabled":True,"entry_coverage":"initialization_and_synthetic_method_contexts",
                    "coverage":"candidate_method_discovery_not_arbitrary_external_requests","roots":[]})
                contract["roots"].extend({**root,"scenario_id":scenario["scenario_id"]}
                                         for root in stats.get("synthetic_method_roots",()))
                aggregate["stats"]["synthetic_method_root_count"]=len(contract["roots"])
            aggregate["stats"]["scenarios"] += 1
            aggregate["stats"]["native_work"] += stats.get("selected_work_items", stats.get("iterations", 0))
            for name in ("global_work_used", "ai_intrinsic_work", "intrinsic_bridge_work", "native_selected_work", "ai_intrinsic_seconds", "input_publications"):
                aggregate["stats"][name] = aggregate["stats"].get(name, 0) + stats.get(name, 0)
            if hasattr(query, "get_unknown_effect_events"):
                events = query.get_unknown_effect_events()
                effects = aggregate["effects"]
                effects.setdefault("havoc", []).extend(event for event in events if event["kind"] == "heap_havoc")
                effects.setdefault("calls", []).extend(event for event in events if event["kind"] == "unknown_callback")
                effects.setdefault("exceptions", []).extend(event for event in events if event["kind"] == "unknown_exception")
            aggregate["phase_trace"].append({"phase": "native", "scenario": scenario["scenario_id"],
                                             "seconds": native_seconds, "work": stats.get("global_work_used", stats.get("iterations", 0)),
                                             "native_selected_work": stats.get("native_selected_work", 0),
                                             "ai_intrinsic_work": stats.get("ai_intrinsic_work", 0),
                                             "intrinsic_bridge_work": stats.get("intrinsic_bridge_work", 0),
                                             "ai_intrinsic_seconds_subset": stats.get("ai_intrinsic_seconds", 0)})
            if not stats.get("converged"): aggregate["solver_status"] = "budget_exhausted"
            for item in query.get_residual_effects():
                aggregate["obligations"].append({str(k): v if type(v) in (str, bool, int, float, type(None), list, dict) else str(v) for k, v in item.items()})
            from pythonstan.ir import IRModule
            for scope in query.get_reachable_scopes():
                if not isinstance(scope, IRModule): continue
                name = scope.get_qualname()
                if name not in modules: continue
                values = aggregate["values"].setdefault(name, {})
                symbols = _names(modules[name]["source"])
                symbols.update(binding.rpartition(".")[2] for binding in config["inputs"]["bindings"]
                               if binding.rpartition(".")[0] == name)
                symbols.update(query.get("symbol") for query in config["queries"]
                               if isinstance(query, dict) and query.get("kind") == "value"
                               and query.get("module") == name and query.get("symbol"))
                for symbol in sorted(symbols):
                    points = query.points_to_at(symbol, scope)
                    if not points and not stats.get("converged"):
                        aggregate["obligations"].append({"kind":"native_pending_value","module":name,
                            "symbol":symbol,"reason":"Empty points-to observation before fixed point is unavailable, not proved Bottom"})
                        continue
                    value = _value(points)
                    if symbol in values:
                        previous = values[symbol]
                        typed = {(type(v).__name__, json.dumps(v, sort_keys=True)): v for v in previous["constants"] + value["constants"]}
                        value["constants"] = [typed[key] for key in sorted(typed)]
                        tagged = {(v["type"], json.dumps(v["value"], sort_keys=True)): v for v in previous["tagged_constants"] + value["tagged_constants"]}
                        value["tagged_constants"] = [tagged[key] for key in sorted(tagged)]
                        value["objects"] = sorted(set(previous["objects"] + value["objects"]))
                        value["types"] = sorted(set(previous["types"] + value["types"]))
                        value["top"] = previous["top"] or value["top"]
                        value["bottom"] = previous["bottom"] and value["bottom"]
                    values[symbol] = value
            for edge in query.call_graph().get_edges():
                site = edge.get_callsite()
                aggregate["calls"].append({"caller": site.scope.stmt.get_qualname(),
                                            "callee": edge.get_callee().stmt.get_qualname(),
                                            "site": str(site.content.statement)})
            aggregate["stats"]["total_work"] = aggregate["stats"].get("global_work_used", 0)
            if publish is not None: publish(aggregate)
    aggregate["calls"] = sorted({json.dumps(x, sort_keys=True): x for x in aggregate["calls"]}.values(), key=lambda x: (x["caller"], x["callee"], x["site"]))
    return aggregate


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            def publish(partial):
                sys.__stdout__.write(json.dumps({"kind": "progress", "result": partial}, allow_nan=False) + "\n")
                sys.__stdout__.flush()
            result = main(payload, publish=publish)
        result["diagnostics"].append({"kind": "legacy_log", "message": log.getvalue()[-2000:],
                                      "truncated": len(log.getvalue())>2000,
                                      "total_characters": len(log.getvalue())}) if log.getvalue() else None
    except Exception:
        result = {"error": traceback.format_exc(), "captured_log": log.getvalue()[-2000:]}
    sys.stdout.write(json.dumps({"kind": "result", "result": result}, allow_nan=False) + "\n")
