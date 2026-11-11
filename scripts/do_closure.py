"""Print closure facts using the maintained static source frontend pipeline."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    path = args.file.resolve()
    if not path.is_file():
        parser.error("file does not exist: " + str(path))
    from pythonstan.world import World
    from pythonstan.world.pipeline import Pipeline
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            Pipeline(config={"filename": str(path), "project_path": str(path.parent),
                             "library_paths": [], "import_level": 0, "mock_libs": False,
                             "time_count": False, "analysis": []})
        rows = [{"qualname": scope.get_qualname(), "cell_variables": sorted(scope.get_cell_vars())}
                for scope in sorted(World().scope_manager.get_scopes(), key=lambda item: item.get_qualname())]
    except (OSError, SyntaxError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps({"schema_version": 1, "file": str(path), "scopes": rows}, sort_keys=True))
    else:
        for row in rows:
            print(row["qualname"] + ": " + ", ".join(row["cell_variables"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
