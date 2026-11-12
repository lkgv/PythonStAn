"""Explicit source targets and immutable execution source inventories."""

from .reuse import input_resolution_contract
from .snapshot import InputError, ParsedTreeCache, SourceSnapshot, snapshot_target
from .targets import File, Project, Source, SourcePath, Target

__all__ = [
    "File",
    "InputError",
    "ParsedTreeCache",
    "Project",
    "Source",
    "SourcePath",
    "SourceSnapshot",
    "Target",
    "input_resolution_contract",
    "snapshot_target",
]
