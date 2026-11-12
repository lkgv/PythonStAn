"""Explicit trusted local capture runner; never invoked by opening a bundle."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
import time
import uuid
from contextlib import redirect_stdout
from io import StringIO
from os import PathLike
from pathlib import Path

from .contract import CaptureRequest, InspectorError, SealedSnapshot, digest


def _inventory(root: Path) -> list[tuple[str, str]]:
    return [
        (p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted(root.rglob("*.py"))
    ]


def run_source(
    source: str | PathLike[str],
    *,
    request: CaptureRequest | None = None,
    max_iterations: int = 50000,
    trace: bool = False,
) -> tuple[SealedSnapshot, list[dict[str, object]] | None, dict[str, float]]:
    # Engine imports are deliberately restricted to this explicit live command.
    from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.world.pipeline import Pipeline
    from pythonstan.world.world import World

    from .adapter import capture

    source = Path(source).resolve(strict=True)
    source_bytes = source.read_bytes()
    code_root = Path(__file__).resolve().parents[1] / "pythonstan"
    code_rows = _inventory(code_root)
    config = Config(
        context_policy="0-cfa",
        max_iterations=max_iterations,
        log_level="ERROR",
        enable_ai_intrinsics=False,
        enable_synthetic_method_contexts=False,
        work_scheduling_policy="fair",
    )
    run_id = str(uuid.uuid4())
    input_record: dict[str, object] = {
        "path": str(source),
        "sha256_before": hashlib.sha256(source_bytes).hexdigest(),
    }
    metadata: dict[str, object] = {
        "run_id": run_id,
        "inputs": [input_record],
        "implementation_sha256": digest(code_rows),
        "configuration": config.to_dict(),
        "entry_points": [str(source)],
        "dependency_policy": "imports_disabled",
        "models": "repository_builtin_models_in_implementation_hash",
        "environment": {"python": sys.version, "platform": platform.platform()},
        "observation_mode": "trace_and_snapshot" if trace else "snapshot",
        "cache": "new_world",
        "source_validation": "pending",
    }
    metadata["configuration_sha256"] = digest(config.to_dict())
    metadata["adapter_implementation_sha256"] = digest(_inventory(Path(__file__).parent))
    metadata["code_identity_policy"] = "content_inventory_authoritative"
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=code_root.parent,
            capture_output=True,
            text=True,
            timeout=2,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=code_root.parent,
            capture_output=True,
            text=True,
            timeout=2,
        )
        metadata["git"] = {
            "head": head.stdout.strip() if head.returncode == 0 else None,
            "dirty": bool(dirty.stdout) if dirty.returncode == 0 else None,
        }
    except (OSError, subprocess.TimeoutExpired):
        metadata["git"] = {"head": None, "dirty": None}
    recorder = None
    started = time.perf_counter()
    World.setup()
    with redirect_stdout(StringIO()):
        pipeline = Pipeline(
            config={
                "filename": str(source),
                "project_path": str(source.parent),
                "library_paths": [],
                "import_level": 0,
                "mock_libs": False,
                "time_count": False,
                "analysis": [
                    {
                        "name": "pta",
                        "id": "PointerAnalysis",
                        "prev_analysis": ["ir"],
                        "options": config.to_dict(),
                    }
                ],
            }
        )
        driver = pipeline.analysis_manager.get_analyzer("pta")
        candidate: object = getattr(driver, "driver", driver)
        if not isinstance(candidate, PointerAnalysis):
            raise InspectorError("capture runner requires the configured native pointer analysis")
        actual = candidate
        actual.solver.enable_inspector_boundaries()
        if trace:
            from .trace import TraceRecorder

            recorder = TraceRecorder()
            recorder.record("run_start", stage="analysis")
            actual.solver.set_work_step_observer(recorder.observe_step)
        pipeline.run()
    elapsed = time.perf_counter() - started
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    input_record["sha256_after"] = after
    metadata["source_validation"] = (
        "pre_post_verified" if after == input_record["sha256_before"] else "changed_during_analysis"
    )
    metadata["source_validation_limit"] = "pre/post digest cannot detect transient edits"
    metadata["implementation_sha256_after"] = digest(_inventory(code_root))
    metadata["implementation_stable"] = (
        metadata["implementation_sha256_after"] == metadata["implementation_sha256"]
    )
    metadata["phases"] = {"analysis_seconds": elapsed}
    if recorder:
        recorder.record(
            "run_stop", stage="analysis", converged=int(actual.solver._stats["converged"])
        )
        metadata["trace_health"] = recorder.health
    start_capture = time.perf_counter()
    snapshot = capture(actual.solver, request or CaptureRequest(), metadata)
    # Do not alter the sealed snapshot with timings observed after its cut.
    metrics = {"analysis_seconds": elapsed, "capture_seconds": time.perf_counter() - start_capture}
    return snapshot, recorder.events if recorder else None, metrics
