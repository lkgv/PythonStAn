"""Retained source-selector identities for frozen inventory reuse."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pythonstan.analysis.contracts.json_data import JsonObject, JsonValue
from pythonstan.config.model import InputsConfig

from .targets import File, Project, Source, SourcePath, Target


def _path(value: SourcePath, *, base: Path | None = None) -> str:
    path = Path(value).expanduser()
    if base is not None and not path.is_absolute():
        path = base / path
    return str(path.resolve())


def _paths(values: Sequence[SourcePath], *, base: Path | None = None) -> list[JsonValue]:
    return [_path(value, base=base) for value in values]


def input_resolution_contract(target: Target, options: InputsConfig) -> JsonObject:
    """Normalize selectors only; never scan directories or read source bytes.

    Entries are compared against frozen scenarios separately. Abstract bindings
    and engine/domain policy may legitimately change on a clean source rerun.
    """
    base = Path(target.root).expanduser().resolve() if isinstance(target, Project) else None
    config: JsonObject = {
        "project_root": None if options.project_root is None else _path(options.project_root),
        "source_roots": _paths(options.source_roots, base=base),
        "exclusions": list(options.exclusions),
    }
    descriptor: JsonObject
    if isinstance(target, Source):
        descriptor = {
            "kind": "source",
            "module": target.module,
            "filename": target.filename,
            "project_root": None if target.project_root is None else _path(target.project_root),
            "import_roots": _paths(target.import_roots),
        }
    elif isinstance(target, File):
        descriptor = {"kind": "file", "path": _path(target.path), "module": target.module or None}
    else:
        descriptor = {
            "kind": "project",
            "root": _path(target.root),
            "source_roots": _paths(target.source_roots, base=base),
            "exclusions": list(target.exclusions),
        }
    return {"target": descriptor, "input_selectors": config}
