"""Pointer analysis module for PythonStAn.

This module provides k-CFA pointer analysis capabilities integrated
with the PythonStAn analysis framework.
"""

from .analyzer import PointerAnalysis, PointerAnalysisDriver
from .kcfa2.analysis import KCFA2PointerAnalysis
from .kcfa2.config import KCFAConfig

__all__ = [
    "PointerAnalysis",
    "PointerAnalysisDriver",
    "KCFA2PointerAnalysis", 
    "KCFAConfig"
]
