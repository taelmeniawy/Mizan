"""Application data locations.

Mizan keeps everything under one data root: the SQLite database, the managed
documents, backups and logs.  The root defaults to ``%LOCALAPPDATA%\\Mizan`` on
Windows (or ``~/.mizan`` elsewhere) and can be overridden with ``MIZAN_DATA_DIR``
or an explicit argument.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @classmethod
    def at(cls, root: str | Path) -> "AppPaths":
        return cls(Path(root).expanduser().resolve())

    @classmethod
    def default(cls) -> "AppPaths":
        override = os.environ.get("MIZAN_DATA_DIR")
        if override:
            return cls.at(override)
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) / "Mizan" if local else Path.home() / ".mizan"
        return cls.at(base)

    @property
    def db_path(self) -> Path:
        return self.root / "mizan.db"

    @property
    def documents_root(self) -> Path:
        return self.root / "documents"

    @property
    def backups_root(self) -> Path:
        return self.root / "backups"

    @property
    def logs_root(self) -> Path:
        return self.root / "logs"

    def ensure(self) -> "AppPaths":
        for directory in (self.root, self.documents_root, self.backups_root, self.logs_root):
            directory.mkdir(parents=True, exist_ok=True)
        return self
