"""Compatibility alias for pythonstan.compat.legacy_ai.config."""
import sys
import pythonstan.compat.legacy_ai.config as _implementation
sys.modules[__name__] = _implementation
