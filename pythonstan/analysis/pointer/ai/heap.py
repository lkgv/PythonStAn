"""Compatibility alias for pythonstan.compat.legacy_ai.heap."""
import sys
import pythonstan.compat.legacy_ai.heap as _implementation
sys.modules[__name__] = _implementation
