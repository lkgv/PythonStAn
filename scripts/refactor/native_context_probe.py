"""Bounded native context/configuration regression probe (adapted from BOOT audit).

Every child starts a fresh World/object table. The production package is imported
from the requested --repo; no production source is patched or rewritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import fields, is_dataclass
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import traceback

CASES = {
    "exception_args": "error=ValueError('known')\nresult=error.args[0]\n",
    "exception_keyword": "error=NameError('message',name='token')\nresult=error.name\n",
    "exception_default_attr": "error=AttributeError()\nresult=error.name\n",
    "exception_stopvalue": "error=StopIteration(7)\nresult=error.value\n",
    "exception_shadow": "class Box: pass\nbox=Box()\ndef ValueError():\n    box.value=9\n    return 7\nignored=ValueError()\nresult=box.value\n",
    "intrinsic_getattr": "class Box: pass\nbox=Box()\nbox.value=7\nresult=getattr(box,'value')\n",
    "exception_constructor": "class Manager: pass\nerror=ValueError('known')\nresult=Manager\n",
    "unknown_heap": "class Box: pass\nbox=Box()\nbox.value=1\neval(\"setattr(box, 'value', 7)\")\nresult=box.value\n",
    "unknown_global": "value=1\nexec('value=7')\nresult=value\n",
    "unknown_cell": "def outer():\n    x=1\n    def change():\n        nonlocal x\n        x=7\n    eval('change()')\n    return x\nresult=outer()\n",
    "mapping_items": "mapping={'read':1}\nfor pair in mapping.items():\n    result=pair[1]\n",
    "super_builtin": "class A:\n    def f(self): return 1\nclass B(A):\n    def f(self): return super().f()\nresult=B().f()\n",
    "builtin_factory": "def make(v): return list((v,))\na=make(1)\nb=make(2)\nleft=a[0]\nright=b[0]\n",
    "args_factory": "def make(*args): return args\ndef invoke(v): return make(v)\na=invoke(1)\nb=invoke(2)\nleft=a[0]\nright=b[0]\n",
    "kwargs_factory": "def make(**kwargs): return kwargs\ndef invoke(v): return make(value=v)\na=invoke(1)\nb=invoke(2)\nleft=a['value']\nright=b['value']\n",
    "closure_factory": "def make(v):\n    def closure():\n        return v\n    return closure\na=make(1)\nb=make(2)\nleft=a()\nright=b()\n",
    "class_factory": "def make(v):\n    class Box:\n        value=v\n    return Box\na=make(1)\nb=make(2)\nleft=a.value\nright=b.value\n",
    "tuple_factory": "def make(v): return (v,)\na=make(1)\nb=make(2)\nleft=a[0]\nright=b[0]\n",
    "dict_factory": "def make(v): return {'value':v}\na=make(1)\nb=make(2)\nleft=a['value']\nright=b['value']\n",
    "generator_factory": "def sequence(v):\n    yield v\ndef make(v): return sequence(v)\na=make(1)\nb=make(2)\nleft=next(a)\nright=next(b)\n",
    "param_recursion": "def f(x):\n    f(x)\nf(1)\n",
    "param_terminating": "def f(x, again):\n    if again:\n        f(x, False)\n    return x\nresult = f(1, True)\n",
    "object_recursion": "class N:\n    def step(self):\n        N().step()\nN().step()\n",
    "object_terminating": "class N:\n    def step(self, again):\n        if again:\n            N().step(False)\n        return 1\nresult = N().step(True)\n",
    "swap": "def first(x, y):\n    return x\na = 1\nb = 2\nleft = first(a, b)\nright = first(b, a)\n",
    "heap_precision": "class Box: pass\ndef make(v):\n    b = Box()\n    b.value = v\n    return b\na = make(1)\nb = make(2)\nleft = a.value\nright = b.value\n",
    "container_precision": "def make(v):\n    return [v]\na = make(1)\nb = make(2)\nleft = a[0]\nright = b[0]\n",
    "kwargs_effect": "events=[]\ndef chosen(box):\n    box.value=7\n    events.append('called')\ndef route(**kw):\n    return kw['cb']\nclass Box: pass\nbox=Box()\nbox.value=0\nfn=route(cb=chosen)\nfn(box)\nresult=box.value\n",
    "index": "x=[1,2]\nleft=x[0]\nright=x[1]\n",
    "dict_index": "x={'a':1,'b':2}\nleft=x['a']\nright=x['b']\n",
    "keyword_partition": "def chosen(): return 1\ndef unchosen(): return 2\nclass M:\n    def keys(self): return ('cb', 'other')\n    def __getitem__(self, k):\n        table = {'cb': chosen, 'other': unchosen}\n        return table[k]\ndef route(cb=None, **unused): return cb\nfn=route(**M())\nresult=fn()\n",
}


def context_structure_depth(obj):
    """Measure nested context-bearing data without repr/hash recursion."""
    stack = [(obj, 0)]
    seen = set()
    result = 0
    while stack:
        item, depth = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        result = max(result, depth)
        if depth >= 500 or len(seen) > 100000:
            return {"depth": result, "visited": len(seen), "truncated": True}
        if isinstance(item, (tuple, frozenset, list)):
            stack.extend((entry, depth + 1) for entry in item)
        elif is_dataclass(item):
            # No scope/IR traversal: only context semantic identity structure.
            names = ("context", "params", "receivers", "objects", "alloc_sites",
                     "class_obj", "inner", "call_sites")
            stack.extend((getattr(item, name), depth + 1)
                         for name in names if hasattr(item, name))
    return {"depth": result, "visited": len(seen), "truncated": False}


def one(args):
    sys.path.insert(0, str(Path(args.repo).resolve()))
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.analysis.pointer.kcfa.object import ConstantObject, FunctionObject
    from pythonstan.world import World
    from pythonstan.world.pipeline import Pipeline
    source = CASES[args.case]
    out = {"case": args.case, "policy": args.policy, "budget": args.budget,
           "scheduler": args.scheduler, "points": args.points,
           "bootstrap": args.bootstrap, "source": source}
    start = time.perf_counter()
    captured = StringIO()
    driver = None
    try:
        World.setup()
        option_values = dict(context_policy=args.policy, max_iterations=args.budget,
                         enable_ai_intrinsics=False,
                         enable_synthetic_method_contexts=False,
                         entry_points=[], log_level="ERROR",
                         index_sensitive=not args.index_insensitive,
                         work_scheduling_policy=args.scheduler,
                         points_work_policy=args.points,
                         builtin_bootstrap_policy=args.bootstrap)
        option_values.update(json.loads(args.config_json))
        options = Config(**option_values)
        with TemporaryDirectory(prefix="pythonstan_native_audit_") as directory:
            path = Path(directory) / "subject.py"
            path.write_text(source)
            with redirect_stdout(captured), redirect_stderr(captured):
                pipeline = Pipeline(config={
                    "filename": str(path), "project_path": directory,
                    "library_paths": [], "import_level": 0, "mock_libs": False,
                    "time_count": False,
                    "analysis": [{"name": "pta", "id": "PointerAnalysis",
                                  "prev_analysis": ["ir"], "options": options.to_dict()}],
                })
                from pythonstan.compat.native_services import configure_native_services
                configure_native_services(pipeline.analysis_manager,max_work=args.budget)
                pipeline.run()
                result = pipeline.analysis_manager.get_results("pta")
                query = result.query()
                driver = pipeline.analysis_manager.get_analyzer("pta")
                driver = getattr(driver, "driver", driver)
            stats = query.get_statistics()
            out["statistics"] = stats
            edges = list(query.call_graph().edges)
            out["call_edges"] = len(edges)
            by_name = Counter(edge.callee.stmt.get_qualname() for edge in edges)
            out["calls_by_name"] = dict(by_name)
            contexts = [edge.callee.context for edge in edges]
            out["context_shapes"] = [context_structure_depth(c) for c in contexts[:10]]
            out["max_context_shape_depth"] = max(
                (context_structure_depth(c)["depth"] for c in contexts), default=0)
            module = World().get_entry_module()
            values = {}
            for name in ("left", "right", "fn", "result"):
                pts = query.points_to_at(name, module)
                values[name] = sorted(
                    [{"kind": type(obj).__name__,
                      "value": obj.value if isinstance(obj, ConstantObject) else
                      obj.alloc_site.stmt.get_qualname() if isinstance(obj, FunctionObject) else None}
                     for obj in pts], key=lambda v: json.dumps(v, sort_keys=True))
            a_pts = query.points_to_at("a", module)
            b_pts = query.points_to_at("b", module)
            out["object_identity_counts"] = {"a":len(a_pts), "b":len(b_pts),
                                             "union_ab":len(a_pts.union(b_pts))}
            out["unknown_effect_events"] = query.get_unknown_effect_events()
            out["residual_effects"] = query.get_residual_effects()
            out["values"] = values
            out["status"] = "ok"
    except BaseException as error:
        out["status"] = "error"
        out["exception"] = type(error).__name__
        out["message"] = str(error)[:1000]
        out["traceback"] = traceback.format_exc(limit=12)
    out["elapsed_seconds"] = round(time.perf_counter() - start, 4)
    out["captured_tail"] = captured.getvalue()[-2000:]
    print(json.dumps(out, sort_keys=True, default=str))


def matrix(args):
    jobs = []
    for case, policies in [
        ("param_recursion", ["0-cfa", "1-cfa", "1-param", "2-param", "3-param"]),
        ("object_recursion", ["1-cfa", "1-obj", "2-obj", "3-obj", "1-rcv", "1-type", "1c1o", "1c2o"]),
        ("swap", ["0-cfa", "1-cfa", "1-param"]),
        ("heap_precision", ["0-cfa", "1-cfa", "1-obj", "1-type", "1-rcv", "1c1o"]),
        ("container_precision", ["0-cfa", "1-cfa", "1-obj", "1-type", "1-rcv", "1c1o"]),
    ]:
        jobs.extend((case, policy) for policy in policies)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as evidence:
        for case, policy in jobs:
            command = [sys.executable, __file__, "--repo", args.repo, "--case", case,
                       "--policy", policy, "--budget", str(args.budget),
                       "--scheduler", args.scheduler]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
                payload = json.loads(result.stdout)
                payload["returncode"] = result.returncode
                if result.stderr:
                    payload["stderr_tail"] = result.stderr[-1000:]
            except subprocess.TimeoutExpired:
                payload = {"case": case, "policy": policy, "budget": args.budget,
                           "status": "subprocess_timeout", "timeout_seconds": args.timeout}
            evidence.write(json.dumps(payload, sort_keys=True) + "\n")
            evidence.flush()
            print(json.dumps({k: payload.get(k) for k in
                              ("case", "policy", "status", "exception", "call_edges", "elapsed_seconds", "max_context_shape_depth")} ), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--case", choices=CASES, default="param_recursion")
    parser.add_argument("--policy", default="1-cfa")
    parser.add_argument("--budget", type=int, default=400)
    parser.add_argument("--scheduler", default="fair")
    parser.add_argument("--points", default="fifo")
    parser.add_argument("--bootstrap", default="queued")
    parser.add_argument("--index-insensitive", action="store_true")
    parser.add_argument("--config-json", default="{}")
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--timeout", type=float, default=8)
    parser.add_argument("--output", default=str(Path(__file__).with_name("context_probes.jsonl")))
    arguments = parser.parse_args()
    matrix(arguments) if arguments.matrix else one(arguments)
