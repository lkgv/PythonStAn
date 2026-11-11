"""Compatibility alias for pythonstan.compat.legacy_ai._trace_partition."""
import sys
import pythonstan.compat.legacy_ai._trace_partition as _implementation
sys.modules[__name__] = _implementation
