"""Compatibility alias for pythonstan.compat.legacy_ai.domains.base."""
import sys
import pythonstan.compat.legacy_ai.domains.base as _implementation
sys.modules[__name__] = _implementation
