#!/usr/bin/env python3
"""Check source-bound candidate summaries; default execution is offline."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataclasses import asdict
from pythonstan.analysis.pointer.ai.certified.driver import analyze_source, read_source, SourceAnalysisConfig
from pythonstan.analysis.pointer.ai.certified import parse_candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--candidate", type=Path)
    mode.add_argument("--llm", action="store_true", help="send source to OpenAI gpt-6-luna")
    parser.add_argument("--no-fallback", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--config", type=Path, help="JSON SourceAnalysisConfig, including nested llm limits")
    parser.add_argument("--inference-max-iterations", type=int)
    parser.add_argument("--checker-max-iterations", type=int)
    args = parser.parse_args(argv)
    try:
        options = json.loads(args.config.read_text()) if args.config else {}
        if not isinstance(options, dict):
            raise ValueError("config must be an object")
        for key in ("inference_max_iterations", "checker_max_iterations"):
            if getattr(args, key) is not None:
                options[key] = getattr(args, key)
        config = SourceAnalysisConfig.from_dict(options)
        candidate = None
        if args.candidate:
            if args.candidate.stat().st_size > config.max_candidate_bytes:
                raise ValueError("candidate exceeds input budget")
            candidate = parse_candidate(json.loads(args.candidate.read_text(encoding="utf-8")))
        report = analyze_source(read_source(args.source, config.max_source_bytes), candidate=candidate,
                                use_llm=args.llm, allow_fallback=not args.no_fallback,
                                analysis_config=asdict(config))
        payload = report.to_dict()
        code = 0 if report.certified else 2
    except (OSError, ValueError, SyntaxError) as exc:
        payload = {"status": "input_error", "certified": False,
                   "diagnostics": [type(exc).__name__]}
        code = 2
    output = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
