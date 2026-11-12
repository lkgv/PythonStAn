"""Completion selection shared by control-flow consumers."""

from pythonstan.analysis.state.flow import Flow, with_state


def resume_pending(pending: Flow, cleanup: Flow) -> Flow:
    """A normal finally restores the pending exit; a new exit replaces it."""
    return with_state(pending, cleanup.state) if cleanup.kind == "normal" else cleanup
