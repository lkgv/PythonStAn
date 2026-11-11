"""Reproduce legacy-Pipeline completion results, reading only AI answers.

Concrete references execute only these trusted fixed miniatures in separate
bounded subprocesses. They never supply facts to the static analyzer.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.object import ConstantObject
from pythonstan.world.pipeline import Pipeline


CASES = {
    "finally_override": "def outer():\n    try:\n        return 1\n    finally:\n        return 2\nresult=outer()\n",
    "return_value_saved": "def outer():\n    x=1\n    try:\n        return x\n    finally:\n        x=2\nresult=outer()\n",
    "nested_override": "def outer():\n    try:\n        try:\n            return 1\n        finally:\n            return 2\n    finally:\n        return 3\nresult=outer()\n",
    "break_cleanup": "def outer():\n    x=0\n    while True:\n        try:\n            break\n        finally:\n            x=2\n    return x\nresult=outer()\n",
    "break_overrides_return": "def outer():\n    while True:\n        try:\n            return 1\n        finally:\n            break\n    return 2\nresult=outer()\n",
    "caught_class": "def outer():\n    try:\n        raise ValueError\n    except ValueError:\n        return 2\nresult=outer()\n",
    "caught_constructor": "def outer():\n    try:\n        raise ValueError()\n    except ValueError:\n        return 2\nresult=outer()\n",
    "pending_exception_restored": "def outer():\n    try:\n        try:\n            raise ValueError\n        finally:\n            try:\n                raise KeyError\n            except KeyError:\n                pass\n    except ValueError:\n        return 2\nresult=outer()\n",
    "with_cleanup": "class Manager:\n    def __init__(self,box):\n        self.box=box\n    def __enter__(self):\n        return self\n    def __exit__(self,a,b,c):\n        self.box[0]=2\n        return False\ndef outer():\n    box=[1]\n    with Manager(box):\n        pass\n    return box[0]\nresult=outer()\n",
}


def production_identity():
    selected = list((ROOT / "pythonstan/analysis/transform").rglob("*.py"))
    selected += list((ROOT / "pythonstan/analysis/pointer/ai").rglob("*.py"))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(selected)}


def concrete_reference(source):
    # The executable source below is fixed in this module, never a user target.
    prelude = "import json,resource\nresource.setrlimit(resource.RLIMIT_AS,(268435456,268435456))\nresource.setrlimit(resource.RLIMIT_CPU,(2,2))\n"
    process = subprocess.run([sys.executable, "-I", "-c", prelude + source + "\nprint(json.dumps(result))\n"],
                             text=True, capture_output=True, timeout=4)
    process.check_returncode()
    return json.loads(process.stdout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    before = production_identity()
    rows = []
    with tempfile.TemporaryDirectory(prefix="pythonstan-completion-") as directory:
        root = Path(directory)
        for name, source in CASES.items():
            case = root / name
            case.mkdir()
            path = case / "subject.py"
            path.write_text(source)
            log = io.StringIO()
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                pipeline = Pipeline(config={"filename":str(path), "project_path":str(case),
                    "library_paths":[], "mock_libs":False, "import_level":0, "time_count":False,
                    "analysis":[
                        {"name":"pta", "id":"PointerAnalysis", "prev_analysis":["ir"],
                         "options":Config(max_iterations=50000, log_level="ERROR",
                             enable_ai_intrinsics=False, enable_synthetic_method_contexts=False).to_dict()},
                        {"name":"ai", "id":"AIAnalysisDriver", "prev_analysis":["pta"],
                         "options":{"type":"ai analysis", "pta_analysis_name":"pta",
                             "plan_scopes":["subject.outer"], "strict_exceptions":True,
                             "enable_narrowing":False, "log_level":"ERROR"}},
                    ]})
                pipeline.run()
                summary = pipeline.analysis_manager.get_results("ai").get_summary("subject.outer")
            constants = [item.value for item in summary.ret if isinstance(item, ConstantObject)]
            full = summary.ret_value
            integers = full.get_int_lattice().get_consts() if full is not None else None
            strings = full.get_const_strs() if full is not None else None
            rows.append({"case":name, "source":source,
                         "source_sha256":hashlib.sha256(source.encode()).hexdigest(),
                         "concrete_reference":concrete_reference(source),
                         "actual_phases":["native", "legacy_ai"],
                         "queried_backend":"AIAnalysisDriver.summary",
                         "ai_address_constants":constants,
                         "ai_full_ints":sorted(integers) if integers is not None else None,
                         "ai_full_strings":sorted(strings) if strings is not None else None,
                         "ai_normal_exit":summary.normal_exit,
                         "ai_complete":summary.is_complete,
                         "incomplete_reasons":list(summary.incomplete_reasons),
                         "may_raise":sorted(summary.may_raise),
                         "prefix_exceptions":sorted(summary.prefix_exceptions),
                         "log":log.getvalue()})
    after = production_identity()
    result = {"schema_version":1, "backend":"native_then_ai legacy Pipeline",
              "production_sha256":before, "production_stable_during_run":before == after,
              "concrete_oracle_is_test_only":True, "rows":rows,
              "limits":["Passing finite witnesses is not a proof of full Python soundness.",
                        "With/async and exception-context boundaries remain open unless their backend transfers are implemented and reviewed."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output":str(args.output), "rows":len(rows), "production_stable":before == after,
                      "open_cases":[row["case"] for row in rows if not row["ai_complete"]]}))


if __name__ == "__main__":
    main()
