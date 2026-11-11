"""Compatibility alias for pythonstan.compat.legacy_ai.summary."""
import sys
import pythonstan.compat.legacy_ai.summary as _implementation
sys.modules[__name__] = _implementation
