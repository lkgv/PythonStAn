"""Compatibility alias for pythonstan.compat.legacy_ai.state."""
import sys
import pythonstan.compat.legacy_ai.state as _implementation
sys.modules[__name__] = _implementation
