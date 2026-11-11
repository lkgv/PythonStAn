"""Compatibility alias for pythonstan.compat.legacy_ai._cfg_adapter."""
import sys
import pythonstan.compat.legacy_ai._cfg_adapter as _implementation
sys.modules[__name__] = _implementation
