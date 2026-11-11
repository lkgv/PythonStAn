"""Run the World/bitmask based engines in an isolated disposable process."""
from __future__ import annotations

from dataclasses import asdict
import json
import subprocess
import sys
import time

from pythonstan.analysis.contracts import thaw_snapshot


def _latest_frame(output):
    if isinstance(output, bytes): output = output.decode("utf-8", errors="replace")
    latest = None
    for line in (output or "").splitlines():
        try: frame = json.loads(line)
        except (ValueError, TypeError): continue
        if isinstance(frame, dict) and frame.get("kind") in {"progress", "result"} and isinstance(frame.get("result"), dict):
            latest = frame["result"]
    return latest


def run_legacy(snapshot, config):
    started = time.monotonic()
    options = config.to_dict() if hasattr(config, "to_dict") else config
    payload = {"modules": [asdict(m) for m in snapshot.modules],
               "scenarios": [{"root_module": s.root_module, "entry_qualname": s.entry_qualname,
                              "arguments": thaw_snapshot(s.arguments), "scenario_id": s.scenario_id}
                             for s in snapshot.scenarios],
               "config": options}
    seconds = options["resources"]["max_seconds"]
    try:
        proc = subprocess.run([sys.executable, "-m", "pythonstan.compat.native_worker"],
                              input=json.dumps(payload), text=True, capture_output=True,
                              timeout=seconds if seconds > 0 else None, check=False)
    except subprocess.TimeoutExpired as exc:
        partial = _latest_frame(exc.stdout)
        if partial is not None:
            partial.update(solver_status="budget_exhausted", coverage_status="open")
            partial.setdefault("obligations", []).append({"kind": "time_budget", "reason": "Legacy wall deadline; completed scenario snapshots retained, current scenario incomplete"})
            partial.setdefault("stats", {})["process_wall_seconds"] = time.monotonic()-started
            observed = partial["stats"].get("global_work_used", 0)
            partial["stats"].update(global_work_observed=observed,
                  unobserved_work_reservation=max(0, options["resources"]["max_work"]-observed),
                  total_work=options["resources"]["max_work"], work_count_status="conservative_reservation")
            partial.setdefault("phase_trace", []).append({"phase": "legacy_process", "status": "time_budget", "partial_export": True})
            return partial
        return {"engine": options["engine"], "solver_status": "budget_exhausted", "coverage_status": "open",
                "values": {}, "calls": [], "returns": {}, "effects": {}, "outcomes": {},
                "obligations": [{"kind": "time_budget", "reason": "Legacy process exceeded global wall time; no partial state exported"}],
                "diagnostics": [], "stats": {"wall_seconds": time.monotonic()-started,
                    "global_work_observed": 0, "unobserved_work_reservation": options["resources"]["max_work"],
                    "total_work": options["resources"]["max_work"], "work_count_status": "conservative_reservation"},
                "phase_trace": [{"phase": "legacy_process", "status": "time_budget"}]}
    if proc.returncode:
        raise RuntimeError("Legacy process failed: " + proc.stderr[-4000:])
    result = _latest_frame(proc.stdout)
    if result is None: raise RuntimeError("Legacy process emitted invalid JSON: " + proc.stdout[-1000:])
    if "error" in result: raise RuntimeError(result["error"])
    result.setdefault("stats", {})["process_wall_seconds"] = time.monotonic() - started
    return result
