"""Fail-closed runner for the frozen mandatory domain/Native fixture set.

``--verify-freeze-only`` checks immutable inputs and exact collection. Normal
execution additionally requires every mandatory test to pass without skips.
W0 can freeze real red evidence; only a later all-green run qualifies G2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

FROZEN_MANIFEST_SHA256 = "3e4275b129680baa743fb1858ebf019ddc5cad20f6f7f18231dc89a9cff60a68"


def object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Expected a JSON object with string keys")
    return dict(value)


def string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("Expected a JSON string array")
    return [str(item) for item in value]


def frozen_suite(repo: Path) -> list[str]:
    raw = (repo / "docs/development/next/domain_native_manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != FROZEN_MANIFEST_SHA256:
        raise ValueError("Mandatory manifest changed without independent review")
    decoded: object = json.loads(raw)
    manifest = object_mapping(decoded)
    nodes = string_list(manifest["mandatory_nodes"])
    if not nodes or len(nodes) != manifest["mandatory_node_count"] or len(set(nodes)) != len(nodes):
        raise ValueError("Frozen mandatory denominator is empty, duplicated, or inconsistent")
    for relative, expected in object_mapping(manifest["fixtures"]).items():
        actual = hashlib.sha256((repo / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Independent fixture changed without review: {relative}")
    files = sorted({node.split("::", 1)[0] for node in nodes})
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--import-mode=importlib",
        "--collect-only",
        "-q",
        *files,
    ]
    collected = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, timeout=15, check=True
    )
    actual_nodes = [line for line in collected.stdout.splitlines() if "::test_" in line]
    if set(actual_nodes) != set(nodes) or len(actual_nodes) != len(nodes):
        raise ValueError(
            "Mandatory fixture collection changed, disappeared, or gained unregistered cases"
        )
    return files


def run_suite(repo: Path, files: list[str]) -> int:
    evidence_root = repo / "artifacts/next/evidence/N4-BOOT-02/domain_native"
    evidence_root.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="acceptance-", dir=evidence_root))
    junit = evidence / "result.junit.xml"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--import-mode=importlib",
        "-p",
        "no:cacheprovider",
        "-q",
        "--tb=short",
        "--junitxml=" + str(junit),
        *files,
    ]
    start = time.monotonic()
    completed = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, timeout=60, check=False
    )
    (evidence / "stdout.txt").write_text(completed.stdout)
    (evidence / "stderr.txt").write_text(completed.stderr)
    root = ET.parse(junit).getroot()
    suites = list(root.iter("testsuite"))
    counts = {
        key: sum(int(suite.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    decoded: object = json.loads(
        (repo / "docs/development/next/domain_native_manifest.json").read_text()
    )
    expected = object_mapping(decoded)["mandatory_node_count"]
    passed = (
        completed.returncode == 0
        and counts["tests"] == expected
        and not any(counts[key] for key in ("failures", "errors", "skipped"))
    )
    receipt = {
        "command": command,
        "cwd": str(repo),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "exit_status": completed.returncode,
        "elapsed_seconds": time.monotonic() - start,
        "counts": counts,
        "mandatory_acceptance": passed,
        "evidence_directory": str(evidence.relative_to(repo)),
    }
    (evidence / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-freeze-only", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    files = frozen_suite(repo)
    if args.verify_freeze_only:
        print(json.dumps({"freeze_integrity": True, "execution_qualified": False, "files": files}))
        return 0
    return run_suite(repo, files)


if __name__ == "__main__":
    raise SystemExit(main())
