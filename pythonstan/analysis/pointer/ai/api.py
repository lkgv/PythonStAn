"""Compatibility alias for pythonstan.compat.legacy_ai.api."""
import sys
import pythonstan.compat.legacy_ai.api as _implementation
sys.modules[__name__] = _implementation
