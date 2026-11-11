"""Completed source controls, distinct from the exact finite-identity control.

The existing audited serializer excludes private temporary and auxiliary PFG
values explicitly. These tests do not claim whole-engine schedule invariance.
"""
from contextlib import redirect_stdout
from io import StringIO
import itertools

import pytest

from benchmark.round10_solver.enqueue_experiment import CASES, abstract_signature
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.world import World
from pythonstan.world.pipeline import Pipeline


def _analyze(filename, policy, depth, index, intrinsics):
    World.setup()
    config = Config(context_policy=f"{depth}-cfa", max_iterations=50000,
        log_level="ERROR", index_sensitive=index, enable_ai_intrinsics=intrinsics,
        enable_synthetic_method_contexts=False, work_scheduling_policy="fair",
        points_work_policy=policy)
    with redirect_stdout(StringIO()):
        pipeline = Pipeline(config={"filename": str(filename), "project_path": str(filename.parent),
            "library_paths": [], "import_level": 0, "mock_libs": False, "time_count": False,
            "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                          "options": config.to_dict()}]})
        pipeline.run()
    query = pipeline.analysis_manager.get_results("pta").query()
    stats = query.get_statistics()
    assert stats["converged"] == 1 and stats["pending_work"] == 0
    return abstract_signature(query, include_facts=True)


@pytest.mark.parametrize("case_id,depth,index,intrinsics", list(itertools.product(
    CASES, range(3), (False, True), (False, True))))
def test_completed_source_projection_preserved(tmp_path, case_id, depth, index, intrinsics):
    filename = tmp_path / "subject.py"
    filename.write_text(CASES[case_id])
    left = _analyze(filename, "fifo", depth, index, intrinsics)
    right = _analyze(filename, "consumer_fair", depth, index, intrinsics)
    assert left == right
