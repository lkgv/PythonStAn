"""Completion selection shared by control-flow consumers."""
from dataclasses import replace

def resume_pending(pending, cleanup):
    """A normal finally restores the pending exit; a new exit replaces it."""
    if cleanup.kind == "normal":
        return replace(pending, store=cleanup.store)
    return cleanup
