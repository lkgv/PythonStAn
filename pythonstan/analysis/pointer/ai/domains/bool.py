"""Compatibility alias for pythonstan.analysis.domains.bool."""
import sys
import pythonstan.analysis.domains.bool as _implementation
sys.modules[__name__] = _implementation
