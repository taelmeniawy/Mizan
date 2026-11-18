"""Phase 10 — application bootstrap, data paths and first run."""

from pathlib import Path

import pytest

from mizan.auth import PERMISSION_DEFS
from mizan.bootstrap import BootstrapError, initialize_first_run, start
from mizan.config import AppPaths
from mizan.document_store import sha256_file
from mizan.documents import DocumentService


def test_paths_default_uses_the_environment_and_creates_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("MIZAN_DATA_DIR", str(tmp_path / "data"))
    paths = AppPaths.default().ensure()
    assert paths.root == (tmp_path / "data").resolve()
    for directory in (paths.root, paths.documents_root, paths.backups_root, paths.logs_root):
        assert directory.is_dir()
    assert paths.db_path == paths.root / "mizan.db"


def test_start_creates_the_schema_and_seed_data(tmp_path):
    context = start(AppPaths.at(tmp_path / "app"))
    assert context.db.scalar("SELECT COUNT(*) FROM schema_migrations") == 10
    assert context.db.scalar("SELECT COUNT(*) FROM roles") == 3
    assert context.db.scalar("SELECT COUNT(*) FROM permissions") == len(PERMISSION_DEFS)
    assert context.db.scalar("SELECT COUNT(*) FROM accounts") >= 20
    assert context.user_count() == 0
    assert context.startup["ok"] is True
    assert context.startup["database"]["schema_state"] == "current"


def test_first_run_creates_the_owner_store_and_audit_event(tmp_path):
    context = start(AppPaths.at(tmp_path / "app"))
    owner = initialize_first_run(
        context,
        store_name="متجر النور",
        display_name="المالك",
        username="owner",
        password="owner-pass-1",
        confirm_password="owner-pass-1",
    )
    assert context.user_count() == 1
    user = context.auth.login("owner", "owner-pass-1")
    assert user["id"] == owner
    assert context.db.scalar("SELECT name_ar FROM store_profile WHERE id=1") == "متجر النور"
    assert context.db.scalar(
        "SELECT COUNT(*) FROM audit_events WHERE event_type='STORE_INITIALIZED'"
    ) == 1
    assert context.has_permission(owner, "manage_users")
    assert context.has_permission(owner, "view_audit_log")

    with pytest.raises(BootstrapError, match="already has users"):
        initialize_first_run(
            context, store_name="آخر", display_name="آخر", username="second",
            password="second-pass-1", confirm_password="second-pass-1",
        )


@pytest.mark.parametrize("overrides, message", [
    ({"store_name": ""}, "required"),
    ({"username": ""}, "required"),
    ({"display_name": ""}, "required"),
    ({"password": "short"}, "at least 8"),
    ({"confirm_password": "different-pass"}, "confirmation"),
])
def test_first_run_validates_its_input(tmp_path, overrides, message):
    context = start(AppPaths.at(tmp_path / "app"))
    values = {
        "store_name": "متجر", "display_name": "المالك", "username": "owner",
        "password": "owner-pass-1", "confirm_password": "owner-pass-1",
    } | overrides
    with pytest.raises(BootstrapError, match=message):
        initialize_first_run(context, **values)
    assert context.user_count() == 0


def test_startup_report_flags_a_missing_document(tmp_path):
    context = start(AppPaths.at(tmp_path / "app"))
    owner = initialize_first_run(
        context, store_name="متجر", display_name="المالك", username="owner",
        password="owner-pass-1", confirm_password="owner-pass-1",
    )
    pdf = context.paths.documents_root / "reports" / "summary.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\nstartup bootstrap document\n%%EOF")
    DocumentService(context.db).record_export(
        document_type="REPORT", document_id=1, export_type="PDF",
        relative_path="reports/summary.pdf", sha256=sha256_file(pdf),
        file_size=pdf.stat().st_size, template_version="T-1", renderer_version="R-1",
        generated_by=owner,
    )
    assert start(context.paths).startup["ok"] is True

    pdf.unlink()
    report = start(context.paths).startup
    assert report["ok"] is False
    assert report["documents"]["missing"] == [{"relative_path": "reports/summary.pdf"}]
    assert not pdf.exists()  # The startup check never regenerates a document.
    assert Path(context.paths.db_path).is_file()
