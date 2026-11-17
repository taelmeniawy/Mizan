"""Scheduled backups, retention and off-device copies.

The scheduled layout is::

    <root>/daily/YYYY-MM-DD[.mizan]
    <root>/monthly/YYYY-MM[.mizan]
    <root>/pre-upgrade/<timestamp>-<label>[.mizan]

Only ``daily`` and ``monthly`` are pruned automatically.  A bundle is deleted
only after the newest bundle in its category has been verified, so a failed or
corrupted run can never remove the last usable backup.  Pre-upgrade bundles are
kept until the owner removes them.

Retention defaults follow ADR-0005 §3.4: 30 daily and 12 monthly bundles.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .backup import BackupError, BackupService


_DAILY = re.compile(r"^\d{4}-\d{2}-\d{2}(\.mizan)?$")
_MONTHLY = re.compile(r"^\d{4}-\d{2}(\.mizan)?$")
_LABEL = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class RetentionPolicy:
    daily: int = 30
    monthly: int = 12

    def __post_init__(self):
        for name in ("daily", "monthly"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} retention must be a positive integer")


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError("today must be a date")


class ScheduledBackups:
    def __init__(self, service: BackupService, root: str | Path,
                 retention: RetentionPolicy | None = None):
        self.service = service
        self.root = Path(root).resolve()
        self.retention = retention or RetentionPolicy()
        documents = service.documents_root
        if self.root == documents or documents in self.root.parents:
            raise BackupError("backup root must be outside managed documents")
        database = service.db.path.resolve()
        if self.root == database or self.root in database.parents:
            raise BackupError("backup root must not contain the active database")

    # ------------------------------------------------------------------
    # Scheduled run
    # ------------------------------------------------------------------
    def run(self, *, actor_id: int, key: bytes | None = None, today=None,
            progress=None) -> dict:
        """Create today's daily bundle and this month's bundle, then prune.

        The monthly bundle is a verified copy of the daily one, so the run takes
        only a single database snapshot.  Re-running the same day is a no-op and
        never overwrites an existing bundle.
        """
        day = _as_date(today or date.today())
        result = {"created": [], "copied": [], "pruned": [],
                  "daily": None, "monthly": None, "skipped": []}

        daily = self._path("daily", day, encrypted=key is not None)
        result["daily"] = str(daily)
        if daily.exists():
            result["skipped"].append("daily backup already exists")
        else:
            self.service.create(daily, actor_id=actor_id, key=key, progress=progress)
            result["created"].append(str(daily))

        monthly = self._path("monthly", day.replace(day=1), encrypted=key is not None)
        result["monthly"] = str(monthly)
        if monthly.exists():
            result["skipped"].append("monthly backup already exists")
        else:
            self.service.copy_to(daily, monthly, actor_id=actor_id, key=key,
                                 progress=progress)
            result["copied"].append(str(monthly))

        result["pruned"] = [str(path) for path in self.prune(key=key, today=day)]
        if result["pruned"]:
            self.service.audit_event(
                "BACKUP_PRUNED", actor_id,
                f"removed {len(result['pruned'])} old bundle(s) after a verified backup",
                self.root,
            )
        return result

    def create_pre_upgrade(self, *, actor_id: int, label: str = "upgrade",
                           key: bytes | None = None, now=None, progress=None) -> Path:
        """Take a backup that retention never deletes (ADR-0005 §3.4)."""
        moment = now or datetime.now()
        if isinstance(moment, date) and not isinstance(moment, datetime):
            moment = datetime(moment.year, moment.month, moment.day)
        cleaned = _LABEL.sub("-", (label or "").strip()).strip("-.")[:40] or "upgrade"
        suffix = ".mizan" if key is not None else ""
        destination = self.root / "pre-upgrade" / f"{moment:%Y%m%dT%H%M%S}-{cleaned}{suffix}"
        return self.service.create(destination, actor_id=actor_id, key=key,
                                   progress=progress)

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------
    def prune(self, *, key: bytes | None = None, today=None, dry_run: bool = False) -> list[Path]:
        """Delete the oldest bundles beyond the retention policy.

        The newest bundle of every pruned category is verified first.  An
        encrypted bundle cannot be verified without its key, so pruning refuses
        to run instead of deleting unverified data.
        """
        del today  # Retention is counted from the newest bundles present.
        removed: list[Path] = []
        for category, keep in (("daily", self.retention.daily),
                               ("monthly", self.retention.monthly)):
            directory = self.root / category
            if not directory.is_dir():
                continue
            bundles = self._bundles(directory, category)
            if len(bundles) <= keep:
                continue
            newest = bundles[-1]
            if newest.is_file() and key is None:
                raise BackupError(
                    "a recovery key is required to verify encrypted backups before pruning"
                )
            self.service.verify(newest, key=key if newest.is_file() else None)
            for path in bundles[:-keep]:
                if dry_run:
                    removed.append(path)
                    continue
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                removed.append(path)
        return removed

    def bundles(self, category: str) -> list[Path]:
        """Return the bundles of a category, oldest first (used by the UI/tests)."""
        return self._bundles(self.root / category, category)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _path(self, category: str, day: date, *, encrypted: bool) -> Path:
        name = day.isoformat() if category == "daily" else f"{day:%Y-%m}"
        return self.root / category / (name + (".mizan" if encrypted else ""))

    @staticmethod
    def _bundles(directory: Path, category: str) -> list[Path]:
        pattern = _DAILY if category == "daily" else _MONTHLY
        found = []
        for path in directory.iterdir():
            if not pattern.match(path.name):
                continue
            if path.is_dir():
                if not (path / "manifest.json").is_file():
                    continue  # never treat a partial directory as a bundle
            elif not path.is_file():
                continue
            found.append(path)
        return sorted(found, key=lambda item: item.name)
