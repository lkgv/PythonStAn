#!/usr/bin/env python3
"""Run the scoped offline release gates. This is not the full-repository gate."""
import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "tests/round11_applications",
    "tests/round11_empirical",
    "tests/round11_formal",
    "tests/round11_integration",
    "tests/round11_liveness",
    "tests/round11_native",
    "tests/round11_scheduler",
    "tests/round10_applications",
    "tests/round10_empirical",
    "tests/round10_formal",
    "tests/round10_frontend",
    "tests/round10_precision",
    "tests/round10_solver",
    "tests/round9_project_engine",
    "tests/round9_frontend",
    "tests/round9_solver",
    "tests/round9_super",
    "tests/round9_formal",
    "tests/round9_objectives",
    "tests/round9_empirical",
    "tests/round8_web_corpus",
    "tests/round8_security",
    "tests/round8_security_formal",
    "tests/round8_scalability",
    "tests/round8_empirical",
    "tests/round7_project_engine",
    "tests/round7_imports",
    "tests/round7_frameworks",
    "tests/round7_formal",
    "tests/round7_web",
    "tests/round7_nonweb",
    "tests/round7_runner",
    "tests/certified",
    "tests/configuration",
    "tests/empirical",
    "tests/realworld_hardcases",
    "tests/round4_formal",
    "tests/round4_empirical",
    "tests/round4_fixpoint",
    "tests/round5_effects",
    "tests/round5_formal",
    "tests/round5_generator",
    "tests/round5_heap_mapping",
    "tests/round5_integration",
    "tests/round5_kcfa_audit",
    "tests/round6_effects",
    "tests/round6_generator",
    "tests/round6_precision",
    "tests/round6_formal",
    "tests/round6_empirical",
    "tests/round6_composition",
    "tests/round6_coroutines",
    "tests/round6_construction",
    "tests/pointer/kcfa/test_ai_bridge.py",
    "tests/pointer/kcfa/test_frontend_lowering.py",
    "tests/analysis/test_ai_domains.py",
    "tests/analysis/test_ai_scopes.py",
    "tests/analysis/test_int_domain.py",
    "tests/analysis/test_ai_precision.py",
    "tests/analysis/test_ai_semantics.py",
    "tests/analysis/test_ai_solver.py",
    "tests/analysis/test_string_domain.py",
    "tests/analysis/test_tuple_modeling.py",
    "tests/analysis/test_ai_interpreter.py",
    "tests/analysis/test_ai_generators.py",
    "tests/analysis/test_ai_solver_real_ir.py",
    "tests/analysis/test_ai_soundness_regressions.py",
    "tests/analysis/test_ai_attribute_algorithms.py",
    "tests/analysis/test_ai_heap_order_v2.py",
    "tests/analysis/test_ai_membership_refinement.py",
    "tests/analysis/test_ai_generator_residuals.py",
    "tests/pointer/kcfa/test_class_protocol.py",
    "tests/pointer/kcfa/precision",
    "tests/pointer/kcfa/test_unknown_tracking.py",
    "tests/pointer/kcfa/test_incremental_constraints.py",
    "tests/pointer/kcfa/test_analysis_module_scopes.py",
    "tests/pointer/kcfa/test_heap_model_temporary_keying.py",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full-suite", action="store_true",
                        help="also run all repository tests, including migrated historical obligations")
    args = parser.parse_args()
    commands = [
        [sys.executable, "-m", "pytest", "-q", *TARGETS],
        [sys.executable, "scripts/run_certified_benchmarks.py", "--mode", "both",
         "--output", "benchmark/certified/results/latest.json"],
    ]
    if args.full_suite:
        commands.append([sys.executable, "-m", "pytest", "-q",
                         "--continue-on-collection-errors", "--tb=short"])
    results = [subprocess.run(command, cwd=ROOT).returncode for command in commands]
    return 0 if all(code == 0 for code in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
