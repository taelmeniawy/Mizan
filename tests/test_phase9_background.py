"""Phase 9 — background execution and cancellation for backup jobs."""

import threading
import time

import pytest

from mizan.backup import BackupService
from mizan.background import BackgroundJob, JobCancelled
from phase6_support import build_shop


@pytest.fixture()
def shop(db, tmp_path):
    shop = build_shop(db)
    documents = tmp_path / "documents"
    documents.mkdir()
    return shop, BackupService(db, documents), tmp_path


def test_backup_job_runs_off_the_caller_thread_and_reports_stages(shop):
    shop, service, tmp_path = shop
    destination = tmp_path / "backup"
    job = BackgroundJob(
        lambda progress, cancel: service.create(
            destination, actor_id=shop.owner, progress=progress),
        name="backup-test",
    )
    started = time.monotonic()
    job.start()
    assert time.monotonic() - started < 0.5  # start() does not block on the backup
    state = job.wait(timeout=60)
    assert state.status == "succeeded"
    assert state.stages[:1] == ("database",)
    assert state.stage == "publish"
    service.verify(state.result)


def test_cancellation_before_publication_leaves_no_bundle(shop):
    shop, service, tmp_path = shop
    destination = tmp_path / "backup"

    def work(progress, cancel):
        def report(stage):
            progress(stage)
            if stage == "documents":
                cancel.set()
        return service.create(destination, actor_id=shop.owner, progress=report)

    job = BackgroundJob(work).start()
    state = job.wait(timeout=60)
    assert state.status == "cancelled"
    assert isinstance(state.error, JobCancelled)
    assert not destination.exists()
    assert not list(tmp_path.glob(".mizan-backup-*"))


def test_the_caller_keeps_running_while_the_job_works(shop):
    shop, service, _ = shop
    release = threading.Event()

    def work(progress, cancel):
        progress("database")
        release.wait(10)
        return "finished"

    job = BackgroundJob(work).start()
    try:
        assert job.is_running
        assert job.state.status == "running"
        deadline = time.monotonic() + 5
        while job.state.stage is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert job.state.stage == "database"
    finally:
        release.set()
    assert job.wait(timeout=10).result == "finished"


def test_failures_and_cancelled_progress_are_reported_not_raised(shop):
    shop, service, _ = shop
    job = BackgroundJob(lambda progress, cancel: 1 / 0).start()
    state = job.wait(timeout=10)
    assert state.status == "failed"
    assert isinstance(state.error, ZeroDivisionError)

    cancelled = BackgroundJob(lambda progress, cancel: None)
    cancelled.cancel()
    with pytest.raises(JobCancelled):
        cancelled.progress("database")
    assert cancelled.state.status == "pending"
    with pytest.raises(RuntimeError, match="already been started"):
        BackgroundJob(lambda progress, cancel: None).start().start()
