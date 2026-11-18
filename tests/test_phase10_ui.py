"""Phase 10 — desktop shell, permissions, pages and background backup actions."""

from datetime import datetime

import pytest
from PySide6.QtCore import Qt

from mizan.audit import AuditService
from mizan.auth import PermissionDenied
from mizan.backup_encryption import generate_key
from mizan.bootstrap import initialize_first_run, start
from mizan.config import AppPaths
from mizan.document_store import sha256_file
from mizan.documents import DocumentService
from mizan.master import MasterDataService
from mizan.ui.audit import AuditPage
from mizan.ui.backup import BackupPage
from mizan.ui.dashboard import DashboardPage
from mizan.ui.diagnostics import DiagnosticsPage
from mizan.ui.login import LoginPage
from mizan.ui.main_window import MainWindow
from mizan.ui.master import MasterDataPage


@pytest.fixture()
def context(tmp_path):
    return start(AppPaths.at(tmp_path / "app"))


@pytest.fixture(autouse=True)
def _dispose_widgets(qt_app):
    """Destroy each test's top-level widgets on the GUI thread.

    QWidget wrappers live in reference cycles (layout -> parent -> child); if
    cyclic GC finalises them while a backup worker thread is running, Qt objects
    are destroyed off the GUI thread.  Explicit disposal keeps teardown on the
    main thread and removes that race from the test process.
    """
    yield
    for widget in qt_app.topLevelWidgets():
        widget.deleteLater()
    qt_app.processEvents()


@pytest.fixture()
def owner_context(context):
    owner = initialize_first_run(
        context, store_name="متجر النور", display_name="المالك",
        username="owner", password="owner-pass-1", confirm_password="owner-pass-1",
    )
    return context, owner


def test_theme_is_rtl_and_uses_the_approved_font(qt_app):
    assert qt_app.layoutDirection() == Qt.RightToLeft
    assert qt_app.font().family() == "Noto Naskh Arabic"
    assert qt_app.applicationDisplayName() == "ميزان"


def test_first_run_login_creates_the_owner_and_opens_the_shell(qt_app, context):
    window = MainWindow(context)
    assert window.stack.currentWidget() is window.login
    page = window.login
    page.store_name.setText("متجر النور")
    page.display_name.setText("المالك")
    page.username.setText("owner")
    page.password.setText("owner-pass-1")
    page.confirm.setText("owner-pass-1")

    assert page.create_owner() is True
    assert window.stack.currentWidget() is window.shell
    assert window.shell.nav.count() == 5
    assert set(window.shell.pages) == {"dashboard", "master", "backup", "audit", "diagnostics"}
    assert isinstance(window.shell.pages["dashboard"], DashboardPage)
    assert isinstance(window.shell.pages["master"], MasterDataPage)
    assert isinstance(window.shell.pages["backup"], BackupPage)
    assert isinstance(window.shell.pages["audit"], AuditPage)
    assert isinstance(window.shell.pages["diagnostics"], DiagnosticsPage)
    assert context.user_count() == 1


def test_login_rejects_a_wrong_password_then_accepts_the_owner(qt_app, owner_context):
    context, owner = owner_context
    page = LoginPage(context)
    assert page.submit_button.text() == "دخول"
    emitted = []
    page.logged_in.connect(emitted.append)

    page.username.setText("owner")
    page.password.setText("wrong-pass")
    assert page.attempt() is False
    assert "غير صحيحة" in page.message.text()

    page.password.setText("owner-pass-1")
    assert page.attempt() is True
    assert emitted == [owner]


def test_shell_navigation_follows_permissions_and_logout_returns_to_login(qt_app, owner_context):
    context, owner = owner_context
    cashier = context.auth.create_user("cashier", "الكاشير", "cashier-pass", ["CASHIER"])
    window = MainWindow(context)
    window.login_succeeded(owner)
    assert window.shell.nav.count() == 5

    window.logout()
    assert window.shell is None
    assert window.stack.currentWidget() is window.login

    window.login_succeeded(cashier)
    assert window.shell.nav.count() == 1
    assert window.shell.nav.item(0).text() == "لوحة التشغيل"
    assert set(window.shell.pages) == {"dashboard"}


def test_dashboard_shows_master_data_counts(qt_app, owner_context):
    context, _ = owner_context
    master = MasterDataService(context.db)
    master.create_unit("PIECE", "قطعة", False)
    master.create_product("P-1", "منتج", "PIECE", "LOT")
    page = DashboardPage(context)
    assert page.values["products"].text() == "1"
    assert page.values["customers"].text() == "0"
    assert page.values["cash"].text() == "0.00 EGP"


def test_audit_page_lists_and_filters_events(qt_app, owner_context):
    context, owner = owner_context
    moment = datetime.now().replace(microsecond=0).isoformat()
    with context.db.transaction() as conn:
        for event_type in ("BACKUP_CREATED", "BACKUP_FAILED", "PERIOD_CLOSED"):
            conn.execute(
                "INSERT INTO audit_events(event_type,entity_type,actor_user_id,occurred_at) "
                "VALUES(?,?,?,?)",
                (event_type, "thing", owner, moment),
            )
    page = AuditPage(context, owner)
    assert page.table.rowCount() == 4  # three events + STORE_INITIALIZED from first run
    assert page.table.columnCount() == 5

    page.event_combo.setCurrentIndex(page.event_combo.findData("BACKUP_CREATED"))
    page._search()
    assert page.table.rowCount() == 1
    assert page.table.item(0, 1).text() == "BACKUP_CREATED"
    assert page.page_label.text() == "عرض 1–1 من 1"

    cashier = context.auth.create_user("cashier", "الكاشير", "cashier-pass", ["CASHIER"])
    with pytest.raises(PermissionDenied):
        AuditService(context.db).query(cashier)


def test_diagnostics_page_shows_a_missing_document(qt_app, owner_context):
    context, owner = owner_context
    pdf = context.paths.documents_root / "reports" / "summary.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\nui diagnostics\n%%EOF")
    DocumentService(context.db).record_export(
        document_type="REPORT", document_id=1, export_type="PDF",
        relative_path="reports/summary.pdf", sha256=sha256_file(pdf),
        file_size=pdf.stat().st_size, template_version="T-1", renderer_version="R-1",
        generated_by=owner,
    )
    pdf.unlink()

    page = DiagnosticsPage(context)
    assert page.report["ok"] is False
    assert "مفقود: 1" in page.documents.text()
    assert "مستند مفقود: reports/summary.pdf" in page.details.toPlainText()
    assert "توجد مشكلات" in page.overall.text()
    assert not pdf.exists()


def test_backup_page_creates_and_verifies_a_backup(qt_app, owner_context):
    context, owner = owner_context
    page = BackupPage(context, owner)
    assert page.daily_label.text().startswith("النسخ اليومية: 0")

    job = page.create_backup()
    assert job is not None
    assert job.wait(120).status == "succeeded"
    page._poll()
    assert "تمت العملية" in page.message.text()
    daily = context.schedule.bundles("daily")
    assert len(daily) == 1
    assert context.backups.verify(daily[0])["format"] == 1
    assert "النسخ اليومية: 1" in page.daily_label.text()

    assert page.verify_latest().wait(60).status == "succeeded"
    page._poll()
    assert "تمت العملية" in page.message.text()


def test_encrypted_backup_from_the_page_requires_a_key(qt_app, owner_context, tmp_path):
    context, owner = owner_context
    page = BackupPage(context, owner)
    page.encrypt.setChecked(True)
    assert page.create_backup() is None
    assert "ملف مفتاح" in page.message.text() or "اختر" in page.message.text()

    key = generate_key()
    key_path = tmp_path / "recovery.key"
    key_path.write_bytes(key)
    page.key_path.setText(str(key_path))
    job = page.create_backup()
    assert job.wait(120).status == "succeeded"
    page._poll()
    daily = context.schedule.bundles("daily")
    assert daily[-1].suffix == ".mizan"
    assert context.backups.verify(daily[-1], key=key)["encrypted"] is True


def test_offsite_copy_from_the_page(qt_app, owner_context, tmp_path):
    context, owner = owner_context
    page = BackupPage(context, owner)
    page.create_backup().wait(120)
    page._poll()
    destination = tmp_path / "external" / "offsite-1"
    job = page.copy_latest_offsite(destination)
    assert job is not None
    assert job.wait(60).status == "succeeded"
    page._poll()
    assert destination.is_dir()
    assert context.backups.verify(destination)
    assert "المسار" in page.message.text()


def test_restore_needs_the_permission_and_a_reason(qt_app, owner_context, tmp_path):
    context, owner = owner_context
    manager = context.auth.create_user("manager", "المدير", "manager-pass", ["MANAGER"])
    manager_page = BackupPage(context, manager)
    assert manager_page.can_restore is False
    assert manager_page.restore_dir_button.isEnabled() is False
    assert manager_page.create_button.isEnabled() is True

    manager_page.create_backup().wait(120)
    manager_page._poll()
    bundle = context.schedule.bundles("daily")[0]
    assert manager_page.restore_bundle(bundle, tmp_path / "rejected", "سبب") is None
    assert "صلاحية" in manager_page.message.text()
    assert not (tmp_path / "rejected").exists()

    owner_page = BackupPage(context, owner)
    assert owner_page.can_restore is True
    assert owner_page.restore_bundle(bundle, tmp_path / "restored", "") is None
    assert "السبب مطلوب" in owner_page.message.text() or "سبب الاستعادة" in owner_page.message.text()

    job = owner_page.restore_bundle(bundle, tmp_path / "restored", "اختبار الاستعادة")
    state = job.wait(120)
    owner_page._poll()
    assert state.status == "succeeded", (state.error, owner_page.message.text())
    assert (tmp_path / "restored" / "mizan.db").is_file()
    assert context.db.scalar(
        "SELECT COUNT(*) FROM audit_events WHERE event_type='DATABASE_RESTORED'"
    ) == 1


def test_master_page_creates_units_products_and_deactivates(qt_app, owner_context):
    context, owner = owner_context
    page = MasterDataPage(context, owner)
    assert page.tabs.count() == 5

    page.units.code.setText("PIECE")
    page.units.name.setText("قطعة")
    assert page.units.add_unit() is True
    assert page.units.table.rowCount() == 1
    assert page.units.table.item(0, 0).text() == "PIECE"

    page.products.refresh()
    assert page.products.unit.findData("PIECE") >= 0
    page.products.code.setText("P-1")
    page.products.name.setText("سلك")
    page.products.unit.setCurrentIndex(page.products.unit.findData("PIECE"))
    page.products.tracking.setCurrentIndex(page.products.tracking.findData("LOT"))
    assert page.products.add_product() is True
    assert page.products.table.rowCount() == 1
    assert page.products.table.item(0, 4).text() == "نشط"

    page.products.table.selectRow(0)
    product_id = context.db.scalar("SELECT id FROM products WHERE code='P-1'")
    page.products.deactivate_product(product_id)
    assert page.products.table.item(0, 4).text() == "معطّل"
    assert context.db.scalar("SELECT is_active FROM products WHERE code='P-1'") == 0


def test_master_page_creates_customers_suppliers_and_projects(qt_app, owner_context):
    context, owner = owner_context
    page = MasterDataPage(context, owner)

    page.customers.code.setText("C-1")
    page.customers.name.setText("مقاول النور")
    page.customers.customer_type.setCurrentIndex(
        page.customers.customer_type.findData("CONTRACTOR")
    )
    assert page.customers.add_customer() is True
    assert page.customers.table.rowCount() == 1
    assert page.customers.table.item(0, 2).text() == "مقاول"

    page.suppliers.code.setText("S-1")
    page.suppliers.name.setText("مورد")
    assert page.suppliers.add_supplier() is True
    assert page.suppliers.table.rowCount() == 1

    contractor_id = context.db.scalar("SELECT id FROM customers WHERE code='C-1'")
    page.projects.refresh()
    assert page.projects.contractor.findData(contractor_id) >= 0
    page.projects.code.setText("PRJ-1")
    page.projects.name.setText("مشروع أ")
    assert page.projects.add_project() is True
    assert page.projects.table.rowCount() == 1
    assert page.projects.table.item(0, 2).text() == "مقاول النور"

    page.customers.code.setText("C-1")
    page.customers.name.setText("مكرر")
    assert page.customers.add_customer() is False
    assert "تعذر" in page.customers.message.text()


def test_master_page_requires_a_contractor_before_projects(qt_app, owner_context):
    context, owner = owner_context
    page = MasterDataPage(context, owner)
    page.customers.code.setText("C-RETAIL")
    page.customers.name.setText("عميل تجزئة")
    page.customers.customer_type.setCurrentIndex(
        page.customers.customer_type.findData("RETAIL")
    )
    assert page.customers.add_customer() is True

    page.projects.refresh()
    assert page.projects.contractor.count() == 0
    page.projects.code.setText("PRJ-X")
    page.projects.name.setText("مشروع")
    assert page.projects.add_project() is False
    assert "مقاول" in page.projects.message.text()


def test_master_page_hides_tabs_without_permission(qt_app, owner_context):
    context, _ = owner_context
    cashier = context.auth.create_user("cashier", "الكاشير", "cashier-pass", ["CASHIER"])
    page = MasterDataPage(context, cashier)
    assert page.tabs.count() == 1  # placeholder only
    assert page.products is None and page.units is None
