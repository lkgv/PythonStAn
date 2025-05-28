import pytest
import os
from pathlib import Path

# Helper function to get the absolute path to benchmark files
def get_benchmark_path(filename):
    project_root = Path(__file__).parent.parent.parent.absolute()
    return project_root / 'benchmark' / filename

# Helper to mark tests that should only run in integration mode
def pytest_addoption(parser):
    parser.addoption(
        "--run-integration", action="store_true", default=False, help="run integration tests"
    )

def pytest_configure(config):
    config.addinivalue_line("markers", "integration: mark test as integration test")

def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        # --run-integration given in cli: do not skip integration tests
        return
    skip_integration = pytest.mark.skip(reason="need --run-integration option to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)

# Fixture for creating basic module and function infrastructure
@pytest.fixture
def mock_module():
    from pythonstan.ir.ir_statements import IRModule
    return IRModule("test_module")

@pytest.fixture
def abstract_state():
    from pythonstan.analysis.ai import AbstractState, ContextType, FlowSensitivity
    return AbstractState(ContextType.CALL_SITE, FlowSensitivity.SENSITIVE)

@pytest.fixture
def abstract_interpreter(abstract_state):
    from pythonstan.analysis.ai import AbstractInterpreter
    return AbstractInterpreter(abstract_state)

@pytest.fixture
def abstract_solver():
    from pythonstan.analysis.ai import create_solver, ContextType, FlowSensitivity
    return create_solver(
        context_type=ContextType.CALL_SITE,
        flow_sensitivity=FlowSensitivity.SENSITIVE,
        context_depth=1,
        max_iterations=50,
        max_recursion_depth=3
    ) 