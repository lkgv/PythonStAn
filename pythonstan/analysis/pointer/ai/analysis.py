"""Compatibility alias for pythonstan.compat.legacy_ai.analysis."""
import sys
import pythonstan.compat.legacy_ai.analysis as _implementation
sys.modules[__name__] = _implementation
