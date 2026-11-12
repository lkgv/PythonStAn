"""Explicit targets: source text is never guessed to be a filename."""

from __future__ import annotations

from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import Literal

type SourcePath = str | PathLike[str]


@dataclass(frozen=True)
class Source:
    code: str
    filename: str = "snippet.py"
    module: str = "snippet"
    project_root: SourcePath | None = None
    import_roots: tuple[SourcePath, ...] = ()
    kind: Literal["source"] = "source"

    def __post_init__(self) -> None:
        if not isinstance(self.code, str):
            raise TypeError("Source.code must be text")
        if not isinstance(self.module, str):
            raise TypeError("Source.module must be text")
        if not self.module:
            raise ValueError("Source.module must be nonempty")
        object.__setattr__(self, "import_roots", tuple(self.import_roots))

    @property
    def config_search_root(self) -> Path | None:
        return None if self.project_root is None else Path(self.project_root)


@dataclass(frozen=True)
class File:
    path: SourcePath
    module: str | None = None
    kind: Literal["file"] = "file"

    def __post_init__(self) -> None:
        if self.module is not None and not isinstance(self.module, str):
            raise TypeError("File.module must be text or None")

    @property
    def config_search_root(self) -> Path:
        return Path(self.path).expanduser().resolve().parent


@dataclass(frozen=True)
class Project:
    root: SourcePath
    entries: tuple[str, ...] = ()
    source_roots: tuple[SourcePath, ...] = ()
    exclusions: tuple[str, ...] = ()
    kind: Literal["project"] = "project"

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(self, "source_roots", tuple(self.source_roots))
        object.__setattr__(self, "exclusions", tuple(self.exclusions))

    @property
    def config_search_root(self) -> Path:
        return Path(self.root)


type Target = Source | File | Project
