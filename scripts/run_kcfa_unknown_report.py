#!/usr/bin/env python3
"""Generate detailed kcfa unknown reports for selected cases."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pythonstan.world.pipeline import Pipeline
from pythonstan.analysis.pointer.kcfa import Config


@dataclass(frozen=True)
class CaseConfig:
    name: str
    filename: Path
    project_path: Path
    import_level: int = 0
    lazy_ir_construction: bool = True
    library_paths: Optional[List[str]] = None


DEFAULT_CASES: Dict[str, CaseConfig] = {
    "dynamic_features": CaseConfig(
        name="dynamic_features",
        filename=REPO_ROOT / "benchmark" / "cases" / "dynamic_features.py",
        project_path=REPO_ROOT / "benchmark" / "cases",
        import_level=0,
        lazy_ir_construction=True,
    ),
    "generators": CaseConfig(
        name="generators",
        filename=REPO_ROOT / "benchmark" / "generators.py",
        project_path=REPO_ROOT / "benchmark",
        import_level=0,
        lazy_ir_construction=True,
    ),
    "control_flow": CaseConfig(
        name="control_flow",
        filename=REPO_ROOT / "benchmark" / "control_flow.py",
        project_path=REPO_ROOT / "benchmark",
        import_level=0,
        lazy_ir_construction=True,
    ),
    "dataflow": CaseConfig(
        name="dataflow",
        filename=REPO_ROOT / "benchmark" / "dataflow.py",
        project_path=REPO_ROOT / "benchmark",
        import_level=0,
        lazy_ir_construction=True,
    ),
}


def build_pipeline_config(case: CaseConfig, options: Dict[str, Any], analysis_name: str) -> Dict[str, Any]:
    return {
        "filename": str(case.filename),
        "project_path": str(case.project_path),
        "import_level": case.import_level,
        "lazy_ir_construction": case.lazy_ir_construction,
        "time_count": False,
        "mock_libs": True,
        "prefer_mock_libs": False,
        "library_paths": case.library_paths or [],
        "analysis": [
            {
                "name": analysis_name,
                "id": "PointerAnalysis",
                "description": f"k-CFA pointer analysis unknown report ({case.name})",
                "prev_analysis": ["ir"],
                "options": options,
            }
        ],
    }


def run_case(case: CaseConfig, args: argparse.Namespace) -> Dict[str, Any]:
    pta_config = Config(
        context_policy=args.context_policy,
        max_iterations=args.max_iterations,
        verbose=False,
        log_level=args.log_level,
        track_unknowns=True,
        log_unknown_details=args.log_unknown_details,
        build_class_hierarchy=True,
        use_mro_resolution=True,
        project_path=str(case.project_path),
        library_paths=case.library_paths or [],
        max_import_depth=case.import_level,
        index_sensitive=args.index_sensitive,
        enable_debug_monitor=False,
        enable_ai_intrinsics=args.enable_ai_intrinsics,
    )

    analysis_name = f"kcfa_unknowns_{case.name}"
    config = build_pipeline_config(case, pta_config.to_dict(), analysis_name)

    pipeline = Pipeline(config=config)
    pipeline.run()

    result = pipeline.analysis_manager.get_results(analysis_name)
    query = result.query()

    return {
        "case": case.name,
        "filename": str(case.filename),
        "project_path": str(case.project_path),
        "config": {
            "context_policy": args.context_policy,
            "index_sensitive": args.index_sensitive,
            "enable_ai_intrinsics": args.enable_ai_intrinsics,
            "track_unknowns": True,
            "log_unknown_details": args.log_unknown_details,
            "max_iterations": args.max_iterations,
            "import_level": case.import_level,
            "lazy_ir_construction": case.lazy_ir_construction,
        },
        "statistics": result.get_statistics(),
        "unknown_summary": query.get_unknown_summary(),
        "unknown_details": query.get_unknown_details(),
    }


def render_markdown(report: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("# KCFA Unknown Report")
    lines.append("")
    lines.append("Generated via `scripts/run_kcfa_unknown_report.py`.")
    lines.append("")
    for entry in report:
        lines.append(f"## Case: {entry['case']}")
        lines.append("")
        lines.append(f"- Entry: `{entry['filename']}`")
        lines.append(f"- Project: `{entry['project_path']}`")
        cfg = entry.get("config", {})
        lines.append(
            "- Config: "
            f"`context_policy={cfg.get('context_policy')}`, "
            f"`index_sensitive={cfg.get('index_sensitive')}`, "
            f"`enable_ai_intrinsics={cfg.get('enable_ai_intrinsics')}`, "
            f"`track_unknowns={cfg.get('track_unknowns')}`, "
            f"`log_unknown_details={cfg.get('log_unknown_details')}`, "
            f"`import_level={cfg.get('import_level')}`, "
            f"`lazy_ir_construction={cfg.get('lazy_ir_construction')}`"
        )
        lines.append("")
        lines.append("### Unknown Summary")
        lines.append("")
        lines.append("```")
        lines.append(json.dumps(entry.get("unknown_summary", {}), indent=2, sort_keys=True))
        lines.append("```")
        lines.append("")
        lines.append("### Unknown Details")
        lines.append("")
        details = entry.get("unknown_details", [])
        if not details:
            lines.append("_No unknowns reported._")
        else:
            lines.append("| Kind | Location | Message | Context |")
            lines.append("| --- | --- | --- | --- |")
            for item in details:
                kind = str(item.get("kind", ""))
                location = str(item.get("location", "")).replace("|", "\\|")
                message = str(item.get("message", "")).replace("|", "\\|")
                context = str(item.get("context", "")).replace("|", "\\|")
                lines.append(f"| {kind} | {location} | {message} | {context} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate kcfa unknown reports.")
    parser.add_argument(
        "--cases",
        nargs="+",
        default=list(DEFAULT_CASES.keys()),
        help=f"Case names to run (default: {', '.join(DEFAULT_CASES.keys())})",
    )
    parser.add_argument(
        "--context-policy",
        default="2-cfa",
        help="Context policy (default: 2-cfa)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=200000,
        help="Max solver iterations (default: 200000)",
    )
    parser.add_argument(
        "--index-sensitive",
        action="store_true",
        help="Enable index sensitivity",
    )
    parser.add_argument(
        "--disable-ai-intrinsics",
        action="store_true",
        help="Disable AI intrinsics in kcfa",
    )
    parser.add_argument(
        "--log-unknown-details",
        action="store_true",
        help="Log unknowns while solving (verbose)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--output-json",
        default=str(REPO_ROOT / "benchmark" / "reports" / "kcfa_unknown_report.json"),
        help="Output JSON report path",
    )
    parser.add_argument(
        "--output-md",
        default=str(REPO_ROOT / "benchmark" / "reports" / "kcfa_unknown_report.md"),
        help="Output markdown report path",
    )

    args = parser.parse_args()

    enable_ai_intrinsics = not args.disable_ai_intrinsics
    args.enable_ai_intrinsics = enable_ai_intrinsics

    selected_cases: List[CaseConfig] = []
    for case_name in args.cases:
        if case_name not in DEFAULT_CASES:
            raise SystemExit(f"Unknown case '{case_name}'. Available: {', '.join(DEFAULT_CASES)}")
        selected_cases.append(DEFAULT_CASES[case_name])

    report: List[Dict[str, Any]] = []
    for case in selected_cases:
        if not case.filename.exists():
            raise SystemExit(f"Case file not found: {case.filename}")
        print(f"Running case: {case.name}")
        report.append(run_case(case, args))

    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, indent=2))

    output_md = Path(args.output_md)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(render_markdown(report))

    print(f"Wrote JSON report to {output_json}")
    print(f"Wrote Markdown report to {output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
