"""Compatibility alias for the canonical integer representation adapter."""
import sys
import pythonstan.compat.legacy_integer_adapter as _implementation
sys.modules[__name__] = _implementation
