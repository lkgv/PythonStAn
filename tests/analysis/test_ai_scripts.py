"""
Tests for AI analysis CLI scripts.

This module provides smoke tests for the CLI scripts to ensure they function
correctly with basic inputs and configurations.
"""

import pytest
import subprocess
import sys
import json
import tempfile
import os
from pathlib import Path
from typing import List, Dict, Any

# Get project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DO_AI_SCRIPT = SCRIPTS_DIR / "do_ai.py"
BENCHMARK_SCRIPT = SCRIPTS_DIR / "run_ai_benchmarks.py"
AI_DYNAMIC_BENCH_SCRIPT = PROJECT_ROOT / "benchmark" / "analyze_ai_dynamic_benchmarks.py"


def run_script(script_path: Path, args: List[str], timeout: int = 60) -> Dict[str, Any]:
    """
    Run a script with given arguments and return results.
    
    Args:
        script_path: Path to the script to run
        args: Command line arguments
        timeout: Timeout in seconds
        
    Returns:
        Dictionary with return_code, stdout, stderr
    """
    # Try to find a clean Python executable
    python_executable = sys.executable
    
    # Check if we're in a problematic Cursor environment
    if 'Cursor' in python_executable or 'AppImage' in python_executable:
        # Try to find system python
        import shutil
        system_python = shutil.which('python3') or shutil.which('python')
        if system_python:
            python_executable = system_python
    
    cmd = [python_executable, str(script_path)] + args
    
    # Set environment to avoid GUI-related errors
    env = os.environ.copy()
    env.pop('DISPLAY', None)
    env.pop('WAYLAND_DISPLAY', None)
    env.pop('XDG_SESSION_TYPE', None)
    env['ELECTRON_DISABLE_SECURITY_WARNINGS'] = 'true'
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    
    # Remove all Electron-related variables
    electron_vars = [k for k in env.keys() if 'ELECTRON' in k or 'CHROME' in k]
    for var in electron_vars:
        env.pop(var, None)
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=PROJECT_ROOT,
            env=env,
            stdin=subprocess.DEVNULL  # Prevent stdin issues
        )
        
        # Combine stdout and stderr since output might be in either
        combined_output = result.stdout + result.stderr
        
        # Filter out common GUI-related warnings
        filtered_stderr = "\n".join([
            line for line in result.stderr.split('\n')
            if not any(pattern in line for pattern in [
                'DISPLAY', 'X server', 'OpenGL', 'EGL', 'ANGLE', 'viz_main',
                'GPU process', 'Wayland', 'libEGL'
            ])
        ])
        
        return {
            "return_code": result.returncode,
            "stdout": result.stdout,
            "stderr": filtered_stderr,
            "combined_output": result.stdout + filtered_stderr,  # Use filtered stderr
            "success": result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {
            "return_code": -1,
            "stdout": "",
            "stderr": "Process timed out",
            "combined_output": "Process timed out",
            "success": False
        }
    except Exception as e:
        return {
            "return_code": -1,
            "stdout": "",
            "stderr": str(e),
            "combined_output": str(e),
            "success": False
        }



def assert_ai_payload(result):
    assert result["return_code"] in (0, 2), result["stderr"]
    payload = json.loads(result["stdout"])
    assert payload["status"] in ("complete", "incomplete"), payload
    assert payload["certified"] is False
    assert payload["summaries"], payload
    assert result["return_code"] == (0 if payload["status"] == "complete" else 2)
    for summary in payload["summaries"].values():
        assert summary["is_complete"] or summary["incomplete_reasons"]
    return payload

FILE_ANALYSIS_SOURCE = '\ndef test_function(x):\n    if x > 0:\n        return x * 2\n    else:\n        return 0\n\ndef main():\n    result = test_function(5)\n    return result\n\nif __name__ == "__main__":\n    main()\n'
COMPLEX_SOURCE = '\nclass Calculator:\n    def __init__(self):\n        self.history = []\n        self.memory = 0\n    \n    def add(self, a, b):\n        result = a + b\n        self.history.append((\'add\', a, b, result))\n        return result\n    \n    def multiply(self, a, b):\n        result = a * b\n        self.history.append((\'multiply\', a, b, result))\n        return result\n    \n    def calculate_expression(self, expr):\n        try:\n            if \'+\' in expr:\n                parts = expr.split(\'+\')\n                return self.add(float(parts[0]), float(parts[1]))\n            elif \'*\' in expr:\n                parts = expr.split(\'*\')\n                return self.multiply(float(parts[0]), float(parts[1]))\n            else:\n                return float(expr)\n        except (ValueError, IndexError):\n            return None\n    \n    def get_history(self):\n        return list(self.history)\n\ndef process_calculations(expressions):\n    calc = Calculator()\n    results = []\n    \n    for expr in expressions:\n        result = calc.calculate_expression(expr)\n        if result is not None:\n            results.append(result)\n    \n    return results, calc.get_history()\n\ndef main():\n    expressions = ["5+3", "2*4", "invalid", "7+1"]\n    results, history = process_calculations(expressions)\n    \n    total = sum(results)\n    return {\n        \'results\': results,\n        \'history\': history,\n        \'total\': total\n    }\n\nif __name__ == "__main__":\n    main()\n'
RECURSIVE_SOURCE = '\ndef factorial(n):\n    if n <= 1:\n        return 1\n    else:\n        return n * factorial(n - 1)\n\ndef fibonacci(n):\n    if n <= 1:\n        return n\n    else:\n        return fibonacci(n - 1) + fibonacci(n - 2)\n\ndef tree_sum(node):\n    if node is None:\n        return 0\n    if isinstance(node, dict):\n        total = node.get(\'value\', 0)\n        for child in node.get(\'children\', []):\n            total += tree_sum(child)\n        return total\n    else:\n        return node\n\ndef main():\n    # Test factorial\n    fact_5 = factorial(5)\n    \n    # Test fibonacci\n    fib_10 = fibonacci(10)\n    \n    # Test tree sum\n    tree = {\n        \'value\': 1,\n        \'children\': [\n            {\'value\': 2, \'children\': []},\n            {\'value\': 3, \'children\': [{\'value\': 4, \'children\': []}]}\n        ]\n    }\n    tree_total = tree_sum(tree)\n    \n    return {\n        \'factorial\': fact_5,\n        \'fibonacci\': fib_10,\n        \'tree_sum\': tree_total\n    }\n\nif __name__ == "__main__":\n    main()\n'
EXCEPTION_SOURCE = '\nclass CustomError(Exception):\n    def __init__(self, message, code=None):\n        super().__init__(message)\n        self.code = code\n\ndef risky_division(a, b):\n    if b == 0:\n        raise CustomError("Division by zero", code=100)\n    return a / b\n\ndef safe_division(a, b):\n    try:\n        return risky_division(a, b)\n    except CustomError as e:\n        print(f"Custom error: {e}, code: {e.code}")\n        return None\n    except Exception as e:\n        print(f"Unexpected error: {e}")\n        return None\n\ndef process_divisions(pairs):\n    results = []\n    errors = []\n    \n    for a, b in pairs:\n        try:\n            result = safe_division(a, b)\n            if result is not None:\n                results.append(result)\n        except Exception as e:\n            errors.append(str(e))\n    \n    return results, errors\n\ndef main():\n    test_pairs = [(10, 2), (5, 0), (8, 4), (3, 0)]\n    results, errors = process_divisions(test_pairs)\n    \n    return {\n        \'results\': results,\n        \'errors\': errors,\n        \'success_count\': len(results)\n    }\n\nif __name__ == "__main__":\n    main()\n'

class TestDoAIScript:
    def test_script_exists(self):
        assert DO_AI_SCRIPT.is_file() and os.access(DO_AI_SCRIPT, os.R_OK)

    def test_help_output(self):
        result = run_script(DO_AI_SCRIPT, ["--help"])
        assert result["success"], result["stderr"]
        assert "--snippet" in result["stdout"] and "--file" in result["stdout"]

    @pytest.mark.parametrize("snippet", ["simple", "conditional", "loop", "objects"])
    def test_snippet_analysis(self, snippet):
        payload = assert_ai_payload(run_script(DO_AI_SCRIPT, ["--snippet", snippet]))
        assert "snippet.main" in payload["summaries"]

    def test_snippet_with_verbose(self):
        result = run_script(DO_AI_SCRIPT, ["--snippet", "simple", "--verbose"])
        payload = assert_ai_payload(result)
        assert payload["effective_config"]["pta_analysis_name"] == "kcfa"
        assert "ContextSelector" in result["stderr"]

    def test_snippet_with_no_pointer(self):
        # Independent AI consumes existing PTA addresses; there is no supported
        # source-analysis mode that silently omits that required predecessor.
        result = run_script(DO_AI_SCRIPT, ["--snippet", "simple", "--no-pointer"])
        assert result["return_code"] == 2
        assert "unrecognized arguments: --no-pointer" in result["stderr"]

    def test_snippet_with_widening(self, tmp_path):
        options = tmp_path / "ai.json"
        options.write_text(json.dumps({"solver_options": {"widen_delay": 1}, "max_iterations": 80}))
        payload = assert_ai_payload(run_script(DO_AI_SCRIPT, ["--snippet", "loop", "--ai-options", str(options)]))
        assert payload["effective_config"]["solver_options"]["widen_delay"] == 1
        assert payload["effective_config"]["max_iterations"] == 80

    def test_dump_ir_to_console(self):
        payload = assert_ai_payload(run_script(DO_AI_SCRIPT, ["--snippet", "simple", "--dump-ir"]))
        assert any(payload["ir"].values())
        assert any("identity" in stmt for body in payload["ir"].values() for stmt in body)

    def test_dump_cfg_to_console(self):
        payload = assert_ai_payload(run_script(DO_AI_SCRIPT, ["--snippet", "simple", "--dump-cfg"]))
        assert payload["cfg"]["snippet.main"]["blocks"]
        assert payload["cfg"]["snippet.main"]["edges"]

    def test_dump_ai_state_to_file(self, tmp_path):
        output = tmp_path / "state.json"
        result = run_script(DO_AI_SCRIPT, ["--snippet", "simple", "--dump-ai-state", str(output)])
        assert output.is_file()
        payload = assert_ai_payload({**result, "stdout": output.read_text()})
        assert "snippet.identity" in payload["summaries"]

    def test_file_analysis_with_temp_file(self, tmp_path):
        path = tmp_path / "subject.py"
        path.write_text(FILE_ANALYSIS_SOURCE)
        payload = assert_ai_payload(run_script(DO_AI_SCRIPT, ["--file", str(path)]))
        assert "subject.test_function" in payload["summaries"]



class TestAIDynamicBenchScript:
    def test_script_exists(self):
        assert AI_DYNAMIC_BENCH_SCRIPT.is_file() and os.access(AI_DYNAMIC_BENCH_SCRIPT, os.R_OK)

    def test_help_output(self):
        result = run_script(AI_DYNAMIC_BENCH_SCRIPT, ["--help"])
        assert result["success"], result["stderr"]
        assert "--feature" in result["stdout"] and "--dry-run" in result["stdout"]

    def test_list_samples(self, tmp_path):
        feature = tmp_path / "ai" / "dynamic_lookup"
        feature.mkdir(parents=True)
        for index in range(1, 5):
            (feature / f"sample_{index}.py").write_text("x = 42\n")
            (feature / f"sample_{index}.json").write_text("{}\n")
        result = run_script(AI_DYNAMIC_BENCH_SCRIPT, [
            "--benchmark-dir", str(tmp_path), "--list-samples", "--max-samples", "3"])
        assert result["success"], result["stderr"]
        assert result["stdout"].splitlines() == [
            "dynamic_lookup/sample_1", "dynamic_lookup/sample_2", "dynamic_lookup/sample_3"]

    def test_list_samples_empty_corpus(self, tmp_path):
        result = run_script(AI_DYNAMIC_BENCH_SCRIPT, ["--benchmark-dir", str(tmp_path), "--list-samples"])
        assert result["success"], result["stderr"]
        assert "No samples found" in result["stdout"]

    def test_invalid_snippet(self):
        result = run_script(DO_AI_SCRIPT, ["--snippet", "nonexistent"])
        assert result["return_code"] == 2 and "invalid choice" in result["stderr"]

    def test_invalid_file(self):
        result = run_script(DO_AI_SCRIPT, ["--file", "/nonexistent/file.py"])
        assert result["return_code"] == 2
        payload = json.loads(result["stdout"])
        assert payload["status"] == "input_or_analysis_error" and payload["diagnostic"]

    def test_k_parameter(self):
        for k in (1, 2, 3):
            payload = assert_ai_payload(run_script(DO_AI_SCRIPT, ["--snippet", "simple", "--k", str(k)]))
            assert payload["effective_pta_config"]["context_policy"] == f"{k}-cfa"

    def test_field_sensitivity_options(self, tmp_path):
        # The supported control is index_sensitive, validated by KCFA Config.
        # Old attr/elem/value/full flags were never wired to an implementation.
        for enabled in (False, True):
            options = tmp_path / "pta.json"
            options.write_text(json.dumps({"index_sensitive": enabled}))
            payload = assert_ai_payload(run_script(DO_AI_SCRIPT, ["--snippet", "objects", "--pta-options", str(options)]))
            assert payload["effective_pta_config"]["index_sensitive"] is enabled
        for obsolete in ("attr", "elem", "value", "full"):
            result = run_script(DO_AI_SCRIPT, ["--snippet", "objects", "--field-sensitivity", obsolete])
            assert result["return_code"] == 2 and "unrecognized arguments" in result["stderr"]

    def test_multiple_dump_options(self):
        payload = assert_ai_payload(run_script(DO_AI_SCRIPT, ["--snippet", "simple", "--dump-ir", "--dump-cfg"]))
        assert payload["ir"]["snippet.main"] and payload["cfg"]["snippet.main"]["edges"]

    def test_containers_snippet(self):
        payload = assert_ai_payload(run_script(DO_AI_SCRIPT, ["--snippet", "containers"]))
        assert "snippet.main" in payload["summaries"]



class TestBenchmarkScript:
    """Execute the real benchmark CLI; incomplete/error rows never become skips."""

    def _run(self, tmp_path, source="x = 1\nresult = x + 2\n", *options):
        path = tmp_path / "sample.py"
        path.write_text(source)
        output = tmp_path / "reports" / "result.json"
        result = run_script(BENCHMARK_SCRIPT, ["--benchmark", str(path), "--output", str(output),
                                                "--timeout", "15", *options], timeout=30)
        assert output.is_file(), result["combined_output"]
        payload = json.loads(output.read_text())
        assert len(payload["detailed_results"]) >= 1
        return result, payload

    def test_script_exists(self):
        assert BENCHMARK_SCRIPT.is_file() and os.access(BENCHMARK_SCRIPT, os.R_OK)

    def test_help_output(self):
        result = run_script(BENCHMARK_SCRIPT, ["--help"])
        assert result["success"], result["combined_output"]
        for option in ("--config-sweep", "--output", "--k", "--obj-depth", "--timeout", "--widening"):
            assert option in result["stdout"]

    def test_list_benchmarks_if_available(self):
        # Test real corpus discovery without depending on arbitrary file order.
        assert (PROJECT_ROOT / "benchmark" / "control_flow.py").is_file()
        from scripts.run_ai_benchmarks import create_benchmark_config
        config = create_benchmark_config(PROJECT_ROOT / "benchmark" / "control_flow.py", {}, {})
        assert [item["id"] for item in config["analysis"]] == ["PointerAnalysis", "AIAnalysisDriver"]
        assert config["analysis"][1]["prev_analysis"] == ["pointer_analysis"]

    def test_output_file_option(self, tmp_path):
        result, payload = self._run(tmp_path)
        assert result["return_code"] == 0, result["combined_output"]
        row = payload["detailed_results"][0]
        assert row["status"] == "completed" and row["success"]
        assert row["metrics"]["pointer_analysis"]["available"]
        ai = row["metrics"]["ai_analysis"]
        assert ai["available"] and ai["complete"] and ai["summaries"]
        assert ai["summaries"]["sample"]["analyzed_contexts"] > 0
        assert not ai["iteration_metrics_available"]
        assert row["resources"]["peak_python_bytes"] > 0
        assert not payload["certified"]

    def test_single_benchmark_if_available(self, tmp_path):
        result, payload = self._run(tmp_path, "def identity(x):\n    return x\nresult = identity('yes')\n")
        assert result["return_code"] in (0, 2), result["combined_output"]
        row = payload["detailed_results"][0]
        assert row["analysis_executed"] and row["error"] is None
        assert row["metrics"]["pointer_analysis"]["total_call_graph_edges"] >= 1
        assert "sample.identity" in row["metrics"]["ai_analysis"]["summaries"]
        assert (result["return_code"] == 0) == (row["status"] == "completed")

    @pytest.mark.parametrize("options, expected", [
        (["--k", "1"], "1-cfa"), (["--obj-depth", "2"], "2-obj"),
    ])
    def test_benchmark_config_options(self, tmp_path, options, expected):
        result, payload = self._run(tmp_path, "result = 3\n", *options, "--widening")
        assert result["return_code"] == 0, result["combined_output"]
        config = payload["detailed_results"][0]["metrics"]["configuration"]
        assert config["pointer"]["context_policy"] == expected
        assert config["ai"]["solver_options"]["widen_delay"] == 0
        assert config["ai"]["solver_options"]["loop_unroll"] == 0

    def test_iteration_limit_reports_incomplete(self, tmp_path):
        result, payload = self._run(tmp_path, "result = 3\n", "--max-iterations", "0")
        assert result["return_code"] == 2
        row = payload["detailed_results"][0]
        assert row["status"] == "incomplete" and not row["success"]
        assert "max_iterations" in row["metrics"]["ai_analysis"]["summaries"]["sample"]["incomplete_reasons"]
        assert payload["overview"]["successful"] == 0

    def test_timeout_has_no_fabricated_metrics(self, tmp_path):
        result, payload = self._run(tmp_path, "result = 3\n", "--timeout", "0.000001")
        assert result["return_code"] == 2
        row = payload["detailed_results"][0]
        assert row["status"] == "timeout" and not row["success"]
        assert row["metrics"] == {}
        assert payload["timing"]["measured_rows"] == 0

    def test_syntax_error_remains_an_error(self, tmp_path):
        result, payload = self._run(tmp_path, "def broken(:\n")
        assert result["return_code"] == 2
        row = payload["detailed_results"][0]
        assert row["status"] == "analysis_error" and "SyntaxError" in row["error"]
        assert not row["success"]

    def test_import_boundary_is_not_counted_as_complete(self, tmp_path):
        (tmp_path / "dependency.py").write_text("value = 9\n")
        result, payload = self._run(tmp_path, "import dependency\nresult = 3\n")
        assert result["return_code"] == 2
        row = payload["detailed_results"][0]
        assert row["status"] == "incomplete"
        assert row["metrics"]["import_coverage"]["unprocessed_import_statements"] == ["import dependency"]

    def test_sweep_executes_every_supported_configuration(self, tmp_path):
        result, payload = self._run(tmp_path, "result = 3\n", "--config-sweep")
        assert result["return_code"] == 0, result["combined_output"]
        rows = payload["detailed_results"]
        assert {row["config_name"] for row in rows} == {"base", "k_1", "k_3", "widening"}
        assert all(row["metrics"]["ai_analysis"]["available"] for row in rows)

    @pytest.mark.parametrize("options, diagnostic", [
        (["--skip-pointer"], "requires KCFA"),
        (["--parallel", "2"], "unsupported"),
        (["--timeout", "0"], "finite positive"),
        (["--field-sensitivity", "elem", "--containers-mode", "precise"], "conflicting"),
    ])
    def test_unsupported_or_conflicting_options_rejected(self, options, diagnostic):
        result = run_script(BENCHMARK_SCRIPT, options)
        assert result["return_code"] == 2
        assert diagnostic in result["stderr"]



class TestAdvancedUseCases:
    def test_error_handling_comprehensive(self, tmp_path):
        for arguments in (["--invalid-arg"], ["--snippet"], ["--file"], ["--k", "invalid"], ["--timeout", "invalid"]):
            assert run_script(DO_AI_SCRIPT, arguments)["return_code"] == 2
        invalid = tmp_path / "bad.json"
        invalid.write_text('{"unknown_option": true}')
        result = run_script(DO_AI_SCRIPT, ["--snippet", "simple", "--pta-options", str(invalid)])
        assert result["return_code"] == 2
        assert json.loads(result["stdout"])["status"] == "input_or_analysis_error"

    def test_analysis_with_complex_code(self, tmp_path):
        source = tmp_path / "subject.py"
        source.write_text(COMPLEX_SOURCE)
        payload = assert_ai_payload(run_script(DO_AI_SCRIPT, ["--file", str(source)]))
        assert any("Calculator" in name for name in payload["summaries"])

    def test_recursive_function_analysis(self, tmp_path):
        source = tmp_path / "subject.py"
        source.write_text(RECURSIVE_SOURCE)
        payload = assert_ai_payload(run_script(DO_AI_SCRIPT, ["--file", str(source)]))
        assert any("factorial" in name for name in payload["summaries"])

    def test_exception_handling_analysis(self, tmp_path):
        source = tmp_path / "subject.py"
        source.write_text(EXCEPTION_SOURCE)
        payload = assert_ai_payload(run_script(DO_AI_SCRIPT, ["--file", str(source)]))
        assert payload["summaries"]



class TestScriptIntegration:
    def test_ai_logging_import(self):
        import logging
        from pythonstan.analysis.pointer.ai.interpreter import logger
        assert isinstance(logger, logging.Logger)
        assert logger.name == "pythonstan.analysis.pointer.ai.interpreter"

    def test_pointer_adapter_import(self):
        from pythonstan.analysis.pointer.ai import PtaQuery
        from pythonstan.analysis.pointer.kcfa.pta_query_adapter import PtaQueryAdapter
        assert callable(PtaQueryAdapter.points_to)
        assert callable(PtaQueryAdapter.field_points_to)
        assert callable(PtaQueryAdapter.get_class_mro)



# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )


# Test utilities for manual testing
def manual_test_do_ai():
    """Manual test function for do_ai.py script."""
    print("Testing do_ai.py script...")
    
    # Test help
    print("\n--- Testing help ---")
    result = run_script(DO_AI_SCRIPT, ["--help"])
    print(f"Help exit code: {result['return_code']}")
    
    # Test simple snippet
    print("\n--- Testing simple snippet ---")
    result = run_script(DO_AI_SCRIPT, ["--snippet", "simple", "--verbose"])
    print(f"Simple snippet exit code: {result['return_code']}")
    if result['stdout']:
        print("STDOUT:", result['stdout'][:500] + "..." if len(result['stdout']) > 500 else result['stdout'])
    if result['stderr']:
        print("STDERR:", result['stderr'][:500] + "..." if len(result['stderr']) > 500 else result['stderr'])


def manual_test_benchmark():
    """Manual test function for benchmark script."""
    print("Testing run_ai_benchmarks.py script...")
    
    # Test help
    print("\n--- Testing help ---")
    result = run_script(BENCHMARK_SCRIPT, ["--help"])
    print(f"Help exit code: {result['return_code']}")


if __name__ == "__main__":
    # Allow running manual tests
    import argparse
    
    parser = argparse.ArgumentParser(description="Manual script testing")
    parser.add_argument("--test", choices=["do_ai", "benchmark", "both"], default="both")
    
    args = parser.parse_args()
    
    if args.test in ["do_ai", "both"]:
        manual_test_do_ai()
    
    if args.test in ["benchmark", "both"]:
        manual_test_benchmark()
