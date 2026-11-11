"""Compatibility alias: canonical closed-call-effects verifier lives in verification."""
import sys
from pythonstan.analysis.verification.closed_effects import checker as _implementation
sys.modules[__name__] = _implementation
