"""Explicit targets: source text is never guessed to be a filename."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Source:
    code: str
    filename: str = "snippet.py"
    module: str = "snippet"
    project_root: str | Path | None = None
    import_roots: tuple[str | Path, ...] = ()
    kind: str = "source"

    def __post_init__(self):
        if not isinstance(self.code, str): raise TypeError("Source.code must be text")
        if not self.module: raise ValueError("Source.module must be nonempty")
        object.__setattr__(self, "import_roots", tuple(self.import_roots))

    @property
    def config_search_root(self): return self.project_root


@dataclass(frozen=True)
class File:
    path: str | Path
    module: str | None = None
    kind: str = "file"

    @property
    def config_search_root(self): return Path(self.path).expanduser().resolve().parent


@dataclass(frozen=True)
class Project:
    root: str | Path
    entries: tuple[str, ...] = ()
    source_roots: tuple[str | Path, ...] = ()
    exclusions: tuple[str, ...] = ()
    kind: str = "project"

    def __post_init__(self):
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(self, "source_roots", tuple(self.source_roots))
        object.__setattr__(self, "exclusions", tuple(self.exclusions))

    @property
    def config_search_root(self): return self.root
