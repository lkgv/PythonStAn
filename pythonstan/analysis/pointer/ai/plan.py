"""Compatibility alias for pythonstan.compat.legacy_ai.plan."""
import sys
import pythonstan.compat.legacy_ai.plan as _implementation
sys.modules[__name__] = _implementation
