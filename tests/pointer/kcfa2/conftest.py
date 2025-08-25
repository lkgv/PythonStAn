"""Fixtures for k-CFA2 pointer analysis tests.

This module provides pytest fixtures for testing the k-CFA2 pointer analysis
implementation in PythonStAn.
"""

import pytest
from typing import List, Tuple, Optional
from pythonstan.analysis.pointer.kcfa2.config import KCFAConfig
from pythonstan.analysis.pointer.kcfa2.context import CallSite, Context
from pythonstan.analysis.pointer.kcfa2.model import AbstractObject, PointsToSet
from pythonstan.analysis.pointer.kcfa2.heap_model import make_object


@pytest.fixture(params=[1, 2])
def k(request):
    """Fixture for k values in k-CFA."""
    return request.param


@pytest.fixture
def obj_depth():
    """Fixture for object sensitivity depth."""
    return 2


@pytest.fixture
def config(k, obj_depth):
    """Fixture for k-CFA configuration."""
    return KCFAConfig(k=k, obj_depth=obj_depth)


@pytest.fixture
def empty_context():
    """Fixture for empty context."""
    return Context()


@pytest.fixture
def make_call_site():
    """Fixture for creating call sites."""
    def _make_call_site(file_path: str, lineno: int, col: int, fn_name: str,
                      bb_name: Optional[str] = None, idx: int = 0) -> CallSite:
        site_id = f"{file_path}:{lineno}:{col}:call"
        return CallSite(site_id=site_id, fn=fn_name, bb=bb_name, idx=idx)
    return _make_call_site


@pytest.fixture
def make_dummy_object():
    """Fixture for creating dummy abstract objects."""
    def _make_dummy_object(alloc_id: str, ctx_length: int = 0) -> AbstractObject:
        # Create a context with synthetic call sites if needed
        context = Context()
        if ctx_length > 0:
            call_sites = []
            for i in range(ctx_length):
                site_id = f"test.py:{100+i}:{10}:call"
                call_site = CallSite(site_id=site_id, fn="test_function")
                call_sites.append(call_site)
            context = Context(tuple(call_sites))
        
        return make_object(alloc_id=alloc_id, alloc_ctx=context)
    return _make_dummy_object


@pytest.fixture
def make_points_to_set():
    """Fixture for creating points-to sets."""
    def _make_points_to_set(objects: List[AbstractObject]) -> PointsToSet:
        return PointsToSet(frozenset(objects))
    return _make_points_to_set