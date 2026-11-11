"""Isolated legacy Pipeline AI-result probe; never executes target source.

Reads one trusted test request from stdin. Native initializes the legacy plan,
but all asserted values come exclusively from the AI summary full-value field.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def main() -> int:
    request = json.load(sys.stdin)
    from pythonstan.world import World
    from pythonstan.world.pipeline import Pipeline
    from pythonstan.analysis.pointer.kcfa.config import Config

    captured = io.StringIO()
    with TemporaryDirectory(prefix="pythonstan_legacy_semantic_probe_") as directory:
        path = Path(directory) / "subject.py"
        path.write_text(request["source"])
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            World.setup()
            pipeline = Pipeline(config={
                "filename": str(path), "project_path": directory, "library_paths": [],
                "import_level": 0, "mock_libs": False, "time_count": False,
                "analysis": [
                    {"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                     "options": Config(max_iterations=50000, log_level="ERROR",
                                       enable_ai_intrinsics=False,
                                       enable_synthetic_method_contexts=False).to_dict()},
                    {"name": "ai", "id": "AIAnalysisDriver", "prev_analysis": ["pta"],
                     "options": {"type": "ai analysis", "pta_analysis_name": "pta",
                                 "plan_scopes": ["subject." + request.get("scope", "outer")],
                                 "strict_exceptions": True, "enable_narrowing": False,
                                 "log_level": "ERROR"}},
                ],
            })
            pipeline.run()
            summary = pipeline.analysis_manager.get_results("ai").get_summary(
                "subject." + request.get("scope", "outer"))
        value = getattr(summary, "ret_value", None)
        row = {
            "query_backend": "legacy_AI_full_summary_value",
            "complete": summary.is_complete,
            "normal_exit": getattr(summary, "normal_exit", None),
            "incomplete_reasons": list(summary.incomplete_reasons),
            "exceptions": sorted(summary.may_raise),
            "full_value_present": value is not None,
            "string_constants": sorted(value.get_const_strs()) if value is not None and value.get_const_strs() is not None else None,
            "integer_constants": sorted(value.get_int_lattice().get_consts()) if value is not None and value.get_int_lattice().get_consts() is not None else None,
            "full_value_bottom": value.is_bottom() if value is not None else None,
            "full_value_repr": repr(value),
            "captured_log": captured.getvalue(),
        }
    print(json.dumps(row, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
