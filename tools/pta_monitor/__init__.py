"""PTA Monitor - Interactive debugger UI for k-CFA pointer analysis.

This package provides a production-grade NiceGUI web application for debugging
and monitoring pointer analysis execution, with a focus on diagnosing missing
call edges and understanding points-to flow.

Main components:
- backend.py: Analysis runner and query APIs
- delta_tracker.py: Iteration-aware delta tracking
- ui.py: NiceGUI frontend implementation
- models.py: Data models for UI

Usage:
    # Run from project root:
    python scripts/run_pta_monitor.py
    
    # Or programmatically:
    from tools.pta_monitor import run_monitor, get_backend
    run_monitor(port=8080)
"""

__version__ = "0.1.0"

# Lazy imports to avoid loading nicegui unless needed
def run_monitor(*args, **kwargs):
    """Run the PTA Monitor web application."""
    from .ui import run_monitor as _run_monitor
    return _run_monitor(*args, **kwargs)

def get_backend():
    """Get the global backend instance."""
    from .backend import get_backend as _get_backend
    return _get_backend()

__all__ = ['run_monitor', 'get_backend', '__version__']

