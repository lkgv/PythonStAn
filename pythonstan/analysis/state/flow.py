"""Compatibility name during the in-place migration of the actual Flow hierarchy.

The only live Flow currently resides with Store. Discriminated completions replace
that class in this module during TYPE-01C; this is not a second outcome model.
"""

from .store import Flow as Flow

__all__ = ["Flow"]
