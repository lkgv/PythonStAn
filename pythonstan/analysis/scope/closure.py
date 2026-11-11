"""Deprecated import of the sole maintained closure-analysis implementation.

The CFG-model-era implementation was unused by the production Pipeline. Keep
this import identity for external callers; no second transfer algorithm lives
here and no new core imports this compatibility location.
"""
from pythonstan.analysis.closure import ClosureAnalysis

__all__ = ["ClosureAnalysis"]
