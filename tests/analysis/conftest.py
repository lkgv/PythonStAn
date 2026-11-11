import pytest
import os
from pathlib import Path
from typing import Dict, Set, List, Optional, Tuple

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
    config.addinivalue_line("markers", "slow: longer-running analysis or CLI test")

def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        # --run-integration given in cli: do not skip integration tests
        return
    skip_integration = pytest.mark.skip(reason="need --run-integration option to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)

# Historical prototype fixtures were replaced by opt-in production harnesses in
# legacy_ai_support.py. No global autouse state reset is installed.
