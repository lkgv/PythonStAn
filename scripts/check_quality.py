"""Run the quality gate from a checkout without installing the analyzer."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    runpy.run_module("scripts.quality.cli", run_name="__main__")
