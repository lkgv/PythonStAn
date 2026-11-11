"""Same-source A-E experiments through the public API, without target execution.

python benchmark/refactor/runner.py --stage preliminary --case django --mode D --output /tmp/run
Official runs additionally require --expected-commit and a clean executable tree.
Every failed, partial, timed out or malformed row is retained with raw receipts.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import tarfile
import time
import traceback
import zipfile

REPO = Path(__file__).resolve().parents[2]
MANIFEST = Path(__file__).with_name("manifest.json")


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def executable_identity():
    files = [p for p in (REPO / "pythonstan").rglob("*")
             if p.is_file() and p.suffix in (".py", ".json")]
    files += [p for p in Path(__file__).parent.glob("*") if p.suffix in (".py", ".json")]
    files += [REPO / "pyproject.toml"]
    hashes = {str(p.relative_to(REPO)): sha256(p) for p in sorted(files)}
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                            capture_output=True, text=True, check=True).stdout.strip()
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return {"git_commit": commit, "code_sha256": digest, "files": hashes}


def _archive_runtime_hashes(archive, case):
    """Ground cached source bytes in the actual pinned archive on every run."""
    parts = PurePosixPath(case["project_root"]).parts
    prefix = "/".join(parts[1:])
    prefix = prefix + "/" if prefix else ""
    runtime = tuple(PurePosixPath(r) for r in case["runtime_source_roots"])
    def selected(name):
        name = name.removeprefix("./")
        if prefix and not name.startswith(prefix): return None
        relative = PurePosixPath(name[len(prefix):])
        if relative.suffix != ".py" or not any(relative == r or r in relative.parents for r in runtime): return None
        return str(relative)
    result = {}
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as packed:
            for info in packed.infolist():
                name = selected(info.filename)
                if name is not None and not info.is_dir(): result[name] = hashlib.sha256(packed.read(info)).hexdigest()
    else:
        with tarfile.open(archive) as packed:
            for member in packed:
                name = selected(member.name)
                if name is not None and member.isfile():
                    stream = packed.extractfile(member)
                    result[name] = hashlib.sha256(stream.read()).hexdigest()
    return result


def prepare_case(case, cache, archives):
    """Extract pinned input bytes only; never install/import/build a package."""
    case = copy.deepcopy(case)
    if case["kind"] == "source":
        case["input_digest"] = hashlib.sha256(case["source"].encode()).hexdigest()
        return case
    if case["kind"] == "generated_project":
        root = cache / case["id"]
        for name, source in case["files"].items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        case["project_path"] = str(root)
    else:
        archive = archives / case["archive_filename"]
        if not archive.is_file():
            raise FileNotFoundError("Missing pinned archive: " + str(archive))
        if sha256(archive) != case["archive_sha256"]:
            raise ValueError("Pinned archive hash mismatch: " + str(archive))
        destination = cache / case["id"]
        stamp = destination / ".extracted-pin.json"
        if not stamp.exists() or json.loads(stamp.read_text()).get("sha256") != case["archive_sha256"]:
            destination.mkdir(parents=True, exist_ok=True)
            if archive.suffix == ".whl":
                with zipfile.ZipFile(archive) as packed:
                    if any(PurePosixPath(n).is_absolute() or ".." in PurePosixPath(n).parts
                           for n in packed.namelist()):
                        raise ValueError("Unsafe archive path")
                    packed.extractall(destination)
            else:
                with tarfile.open(archive) as packed:
                    packed.extractall(destination, filter="data")
            stamp.write_text(json.dumps({"sha256": case["archive_sha256"]}))
        case["project_path"] = str(cache / case["project_root"])
    root = Path(case["project_path"])
    if not root.is_dir():
        raise ValueError("Pinned project root absent: " + str(root))
    runtime_roots = case.get("runtime_source_roots", ["controlpkg"])
    hashes = {str(p.relative_to(root)): sha256(p)
              for source_root in runtime_roots for p in sorted((root / source_root).rglob("*.py"))}
    if len(hashes) != case["expected_inventory_count"]:
        raise ValueError(f"Runtime source inventory changed: {len(hashes)} != {case['expected_inventory_count']}")
    if case["kind"] == "corpus":
        archived = _archive_runtime_hashes(archive, case)
        if archived != hashes:
            changed = sorted(name for name in archived.keys() | hashes.keys() if archived.get(name) != hashes.get(name))
            raise ValueError("Cached runtime source differs from pinned archive: " + repr(changed[:10]))
    reference = case.get("reference_fact")
    if reference and reference.get("source_sha256"):
        if sha256(root / reference["file"]) != reference["source_sha256"]:
            raise ValueError("Frozen source reference changed")
    case["input_files_sha256"] = hashes
    case["input_digest"] = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return case


class AnalysisWallTimeout(Exception):
    pass


def root_exclusion_patterns(names):
    """Exclude distribution siblings, preserving same-named package files.

    The public API accepts component exclusions as well as root-relative glob
    patterns. Escape the first character into a glob class so ``tasks.py``
    means the root sibling, never ``invoke/tasks.py``. The subtree pattern
    covers root directories without dropping bundled package tests/builds.
    """
    patterns = []
    for name in names:
        if not name or "/" in name or name[0] in "[]":
            raise ValueError("Expected an ordinary top-level distribution sibling")
        anchored = "[" + name[0] + "]" + name[1:]
        patterns.extend((anchored, anchored + "/*"))
    return tuple(patterns)


def _measure_method(cls, name, label, ledger):
    original = getattr(cls, name, None)
    if original is None:
        return
    depth = 0
    def measured(*args, **kwargs):
        nonlocal depth
        import resource
        start = time.monotonic()
        depth += 1
        try:
            return original(*args, **kwargs)
        finally:
            depth -= 1
            item = ledger.setdefault(label, {"calls": 0, "inclusive_seconds": 0.0,
                                             "peak_self_rss_kib_cumulative": 0})
            item["calls"] += 1
            if depth == 0:
                item["inclusive_seconds"] += time.monotonic() - start
            item["peak_self_rss_kib_cumulative"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    setattr(cls, name, measured)


def worker(request):
    """One child process owns one run and its OS limits."""
    import resource
    resources = request["resources"]
    cap = int(resources["max_memory_mb"] * 1024 * 1024)
    if cap:
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    start = time.monotonic()
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(AnalysisWallTimeout("global analysis wall budget")))
    signal.setitimer(signal.ITIMER_REAL, resources["max_seconds"])
    ledger = {}
    row = {"record_schema_version": 1, "case_id": request["case"]["id"], "mode": request["mode"],
           "stage": request["stage"], "input_digest": request["case"]["input_digest"],
           "executable": request["executable"], "target_code_executed": False,
           "target_dependencies_installed": False, "reference_fact": request["case"].get("reference_fact")}
    try:
        from pythonstan import Source, Project, ValueQuery, analyze
        from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
        from pythonstan.analysis.composition.oracle import IncrementalCandidateOracle
        from pythonstan.analysis.composition.state_adapter import FullStateAdapter
        _measure_method(InterproceduralDriver, "__init__", "abstract_frontend", ledger)
        _measure_method(InterproceduralDriver, "run", "abstract_inclusive", ledger)
        _measure_method(IncrementalCandidateOracle, "_solve", "oracle_inclusive", ledger)
        for name in ("encode", "decode", "reabstract"):
            _measure_method(FullStateAdapter, name, "adapter_" + name, ledger)
        mode = request["mode_spec"]
        if mode.get("forbid_legacy_native"):
            from pythonstan.world.pipeline import Pipeline
            from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
            import pythonstan.compat.legacy_runner as legacy
            def forbidden(*args, **kwargs):
                raise AssertionError("No-PTA sentinel: hidden legacy Native/PTA execution")
            Pipeline.__init__ = forbidden
            PointerSolver.__init__ = forbidden
            legacy.run_legacy = forbidden
            row["no_legacy_native_sentinel"] = True
        case = request["case"]
        if case["kind"] == "source":
            target = Source(case["source"], filename=case["filename"], module=case["module"])
        else:
            root = Path(case["project_path"])
            target = Project(root, source_roots=tuple(str((root / r).resolve()) for r in case["import_roots"]),
                             exclusions=root_exclusion_patterns(case["exclusions"]))
        query = ValueQuery(**case["query"]) if case.get("query") else None
        queries = tuple(q for q in (query, "calls", "effects", "stats", "outcomes") if q is not None)
        config = copy.deepcopy(request["shared_config"])
        config["engine"] = mode["engine"]
        config["andersen"]["enable_ai_intrinsics"] = mode["native_intrinsics"]
        config["resources"] = {k: resources[k] for k in ("max_work", "max_seconds", "max_memory_mb")}
        result = analyze(target, config=config, queries=queries)
        frozen = result.raw_snapshot()
        answer = result.query(query).to_dict() if query is not None else None
        row.update(capture_status="complete", process_status="valid_result", result=frozen,
                   answer=answer, solver_status=result.solver_status, coverage_status=result.coverage_status,
                   proof_status=result.proof_status, phase_measurements=ledger)
        if case["kind"] == "source":
            observed_input_digest = hashlib.sha256(case["source"].encode()).hexdigest()
        else:
            observed_files = {str(Path(item["filename"]).relative_to(Path(case["project_path"]))): item["original_bytes_digest"]
                              for item in frozen["inventory"]}
            observed_input_digest = hashlib.sha256(json.dumps(observed_files, sort_keys=True).encode()).hexdigest()
        row["observed_input_digest"] = observed_input_digest
        row["input_identity_matches"] = observed_input_digest == case["input_digest"]
        if not row["input_identity_matches"]:
            raise AssertionError("Actual public API inventory differs from the frozen full runtime input")
        if mode.get("forbid_legacy_native"):
            phases = frozen.get("phase_trace", [])
            if any(p.get("pta_started") is True or p.get("phase") == "native" for p in phases):
                raise AssertionError("No-PTA sentinel: actual phase trace reveals full Native predecessor")
        reference = case.get("reference_fact")
        quality = {"query_status": answer["status"] if answer else "no_reference_query",
                   "reference_kind": reference.get("kind") if reference else None,
                   "not_top": False, "contains_reference": None, "exact_reference": None,
                   "useful_reference_hit": False, "proof_claim": False}
        if answer and answer["status"] == "answered" and reference:
            value = answer["value"]
            expected = reference["expected"]
            contains = any(type(x) is type(expected) and x == expected for x in value.get("constants", []))
            not_top = (not value.get("top", True) and not value.get("bottom", True)
                       and not value.get("top_sorts") and not value.get("may_unbound")
                       and "unbound" not in value.get("types", []))
            exact = (contains and not_top and len(value.get("constants", [])) == 1
                     and not value.get("objects") and not value.get("integer_interval")
                     and not value.get("string_patterns"))
            quality.update(not_top=not_top, contains_reference=contains,
                           exact_reference=exact,
                           useful_reference_hit=exact and reference["kind"] in ("pure_module_literal", "controlled_semantics"))
        row["quality"] = quality
    except BaseException as error:
        category = "timeout" if isinstance(error, AnalysisWallTimeout) else "memory_error" if isinstance(error, MemoryError) else "input_error" if type(error).__name__ in ("InputError", "ConfigurationError") else "analysis_error"
        row.update(capture_status="complete_error", process_status=category,
                   exception=type(error).__name__, message=str(error), traceback=traceback.format_exc(),
                   phase_measurements=ledger)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    row["cost"] = {"worker_inclusive_wall_seconds": time.monotonic()-start,
                   "cpu_user_seconds_self": own.ru_utime, "cpu_system_seconds_self": own.ru_stime,
                   "cpu_user_seconds_children": children.ru_utime, "cpu_system_seconds_children": children.ru_stime,
                   "peak_self_rss_kib": own.ru_maxrss, "peak_largest_child_rss_kib": children.ru_maxrss,
                   "memory_limit_mib": resources["max_memory_mb"],
                   "scope": "Includes source normalization/front end/core/oracle/adapter; parent receipt wall additionally includes process start and JSON export. Phase clocks are nested, not additive."}
    return row


def _process_tree_rss(pid):
    total, pending, visited = 0, [pid], set()
    page = os.sysconf("SC_PAGE_SIZE")
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        try:
            total += int(Path(f"/proc/{current}/statm").read_text().split()[1]) * page
            pending.extend(int(x) for x in Path(f"/proc/{current}/task/{current}/children").read_text().split())
        except (OSError, ValueError, IndexError):
            continue
    return total


def run_one(case, mode, options, manifest, identity, preparation_error=None):
    output = options.output / case["id"] / mode
    output.mkdir(parents=True, exist_ok=True)
    before = executable_identity()
    request = {"case": case, "mode": mode, "mode_spec": manifest["modes"][mode],
               "resources": manifest["resources"], "shared_config": manifest["shared_algorithm_configuration"],
               "stage": options.stage, "executable": {k: identity[k] for k in ("git_commit", "code_sha256")}}
    (output / "request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2))
    command = [sys.executable, str(Path(__file__).resolve()), "--worker"]
    if preparation_error:
        receipt = {"case_id": case["id"], "mode": mode, "process_status": "preparation_error",
                   "capture_status": "not_started", "exception": preparation_error,
                   "stage": options.stage, "executable": request["executable"]}
    else:
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = "0"
        environment["PYTHONPATH"] = str(REPO)
        start = time.monotonic()
        sampled_peak = 0
        killed = False
        memory_killed = False
        with (output / "request.json").open() as stdin, (output / "stdout.json").open("w") as stdout, (output / "stderr.txt").open("w") as stderr:
            process = subprocess.Popen(command, cwd=REPO, env=environment, stdin=stdin,
                                       stdout=stdout, stderr=stderr, start_new_session=True)
            while process.poll() is None:
                sampled_peak = max(sampled_peak, _process_tree_rss(process.pid))
                memory_killed = sampled_peak > manifest["resources"]["max_memory_mb"] * 1024 * 1024
                if memory_killed or time.monotonic()-start > manifest["resources"]["outer_timeout_seconds"]:
                    killed = True
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    break
                time.sleep(0.05)
            process.wait()
        try:
            receipt = json.loads((output / "stdout.json").read_text())
        except (OSError, ValueError):
            receipt = {"case_id": case["id"], "mode": mode, "process_status": "memory_budget" if memory_killed else "outer_timeout" if killed else "capture_error",
                       "capture_status": "partial_or_missing", "stage": options.stage,
                       "executable": request["executable"]}
        receipt["process_receipt"] = {"command": command, "cwd": str(REPO), "exit_code": process.returncode,
                                      "outer_wall_seconds": time.monotonic()-start,
                                      "sampled_peak_process_tree_rss_bytes": sampled_peak,
                                      "sampling_interval_seconds": 0.05,
                                      "memory_observation_limit": "Sampled peak is a lower bound; own/child kernel ru_maxrss are reported separately.",
                                      "outer_safeguard_killed": killed,
                                      "memory_budget_killed": memory_killed,
                                      "memory_enforcement": "Per-process RLIMIT_AS plus observed 50ms full-process-tree RSS cap; brief unsampled peaks remain a measurement limitation.",
                                      "stdout_bytes": (output / "stdout.json").stat().st_size}
        if killed or process.returncode:
            receipt["process_status"] = "memory_budget" if memory_killed else "outer_timeout" if killed else "process_failed"
            receipt["capture_status"] = "failed_process_even_if_json_present"
    after = executable_identity()
    receipt["input_digest"] = case.get("input_digest")
    receipt["category"] = case["category"]
    receipt["expected_inventory_count"] = case.get("expected_inventory_count", 1)
    receipt["code_unchanged"] = before["code_sha256"] == after["code_sha256"] == identity["code_sha256"]
    receipt["commit_unchanged"] = before["git_commit"] == after["git_commit"] == identity["git_commit"]
    receipt["matched_version_valid"] = receipt["code_unchanged"] and receipt["commit_unchanged"]
    receipt["executable_before"] = {key: before[key] for key in ("git_commit", "code_sha256")}
    receipt["executable_after"] = {key: after[key] for key in ("git_commit", "code_sha256")}
    (output / "record.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt


def main(argv=None):
    if argv is None and sys.argv[1:] == ["--worker"]:
        print(json.dumps(worker(json.load(sys.stdin)), ensure_ascii=False, allow_nan=False))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=REPO.parents[1] / "refactor_corpus")
    parser.add_argument("--archives", type=Path, default=REPO.parent / "corpus_archives")
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--mode", action="append", choices=tuple("ABCDE"), dest="modes")
    parser.add_argument("--stage", choices=("preliminary", "official"), default="preliminary")
    parser.add_argument("--expected-commit")
    parser.add_argument("--workers", type=int)
    args = parser.parse_args(argv)
    args.output = args.output.resolve()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Use a new empty output directory so existing raw evidence cannot be overwritten")
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text())
    workers = manifest["resources"]["parallel_workers"] if args.workers is None else args.workers
    if workers < 1 or workers > manifest["resources"]["parallel_workers"]:
        parser.error("workers must be within the frozen global parallel limit")
    cases = manifest["cases"]
    if args.cases:
        if set(args.cases)-{case["id"] for case in cases}:
            parser.error("Unknown case IDs")
        cases = [case for case in cases if case["id"] in args.cases]
    identity = executable_identity()
    if args.stage == "official":
        if not args.expected_commit or identity["git_commit"] != args.expected_commit:
            parser.error("Official experiments require the exact frozen --expected-commit")
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "pythonstan", "pyproject.toml", "benchmark/refactor"],
                               cwd=REPO, capture_output=True, text=True, check=True).stdout
        if dirty:
            parser.error("Official executable tree must be committed and clean: " + dirty)
    prepared, failures = {}, {}
    setup_start = time.monotonic()
    for case in cases:
        try:
            prepared[case["id"]] = prepare_case(case, args.cache, args.archives)
        except Exception as error:
            prepared[case["id"]] = case
            failures[case["id"]] = type(error).__name__ + ": " + str(error)
    run_manifest = {"schema_version": 1, "stage": args.stage, "executable": identity,
                    "protocol": manifest, "selected_cases": [case["id"] for case in cases],
                    "selected_modes": args.modes or list("ABCDE"), "python": sys.version,
                    "actual_parallel_workers":workers,
                    "input_preparation_seconds": time.monotonic()-setup_start,
                    "input_preparation_failures": failures,
                    "input_identities": {k: {"digest": v.get("input_digest"), "files": v.get("input_files_sha256")} for k, v in prepared.items()}}
    (args.output / "run_manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2))
    jobs = [(prepared[case["id"]], mode) for case in cases for mode in run_manifest["selected_modes"]]
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_one, case, mode, args, manifest, identity, failures.get(case["id"])): (case["id"], mode) for case, mode in jobs}
        for future in as_completed(futures):
            case_id, mode = futures[future]
            try:
                row = future.result()
            except Exception as error:
                row = {"case_id": case_id, "mode": mode, "process_status": "orchestrator_error", "exception": repr(error), "traceback": traceback.format_exc()}
                path = args.output / case_id / mode
                path.mkdir(parents=True, exist_ok=True)
                (path / "record.json").write_text(json.dumps(row, indent=2))
            rows.append(row)
            print(json.dumps({"case": case_id, "mode": mode, "process": row["process_status"],
                              "solver": row.get("solver_status"), "coverage": row.get("coverage_status"),
                              "quality": row.get("quality", {})}), flush=True)
    run_manifest["completed_rows"] = len(rows)
    run_manifest["expected_rows"] = len(jobs)
    run_manifest["all_executable_identities_stable"] = all(row.get("matched_version_valid", False) for row in rows)
    (args.output / "run_manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2))
    from aggregate import aggregate
    summary = aggregate(args.output)
    print(json.dumps({"rows": len(rows), "summary": str(args.output / "summary.json"),
                      "same_version_valid": run_manifest["all_executable_identities_stable"]}))
    return 0 if len(rows) == len(jobs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
