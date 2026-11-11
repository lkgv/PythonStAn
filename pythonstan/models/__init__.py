"""Versioned declarations shared by source semantics and analysis backends."""

from .builtins import (ABSTRACT_BUILTINS, EXCEPTION_TYPES, LEGACY_BUILTIN_FUNCTIONS,
                       MODEL_VERSION, PYTHON312_BUILTIN_NAMES)
from .exceptions import EXCEPTION_NAMES, builtin_exception_ancestors

__all__ = ["ABSTRACT_BUILTINS", "EXCEPTION_TYPES", "LEGACY_BUILTIN_FUNCTIONS", "MODEL_VERSION",
           "PYTHON312_BUILTIN_NAMES", "EXCEPTION_NAMES", "builtin_exception_ancestors"]
