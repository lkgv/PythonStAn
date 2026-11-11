"""Compatibility alias for a historically misnamed unused closure module.

Class hierarchy modeling is owned by the selected engine. This old module had
an unfinished duplicate ClosureAnalysis; supported closure callers now receive
the canonical implementation rather than a second registered analysis class.
"""
from pythonstan.analysis.closure import ClosureAnalysis

__all__ = ["ClosureAnalysis"]
