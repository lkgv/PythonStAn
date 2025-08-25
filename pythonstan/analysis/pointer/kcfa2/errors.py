"""Exception classes for k-CFA pointer analysis.

This module defines specific exception classes for various error conditions
that can occur during k-CFA pointer analysis with 2-object sensitivity.
"""

__all__ = [
    "KCFAError",
    "ConfigurationError", 
    "IRAdapterError",
    "AnalysisTimeout",
    "SoundnessWarning"
]


class KCFAError(Exception):
    """Base exception for k-CFA pointer analysis errors."""
    pass


class ConfigurationError(KCFAError):
    """Raised when analysis configuration is invalid or unsupported.
    
    Examples:
    - Invalid k or obj_depth values
    - Unsupported field sensitivity modes
    - Missing required configuration parameters
    """
    pass


class IRAdapterError(KCFAError):
    """Raised when IR/TAC adaptation fails.
    
    Examples:
    - Unrecognized IR node types
    - Missing source location information  
    - Inconsistent IR structure
    - Unsupported language constructs
    """
    pass


class AnalysisTimeout(KCFAError):
    """Raised when analysis exceeds configured timeout.
    
    This exception indicates that the analysis did not converge
    within the specified time limit and was forcibly terminated.
    """
    pass


class SoundnessWarning(Warning):
    """Warning for potential soundness issues.
    
    This warning is issued when the analysis makes unsound assumptions
    or approximations that might affect correctness.
    
    Examples:
    - Conservative approximations for dynamic features
    - Unmodeled external library functions
    - Heap abstraction widening
    - Missing call targets
    """
    pass