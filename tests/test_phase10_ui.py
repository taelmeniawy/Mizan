"""Phase 10 — desktop shell, permissions, pages and background backup actions."""

from datetime import datetime

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QInputDialog, QMessageBox, QPushButton

from mizan.audit import AuditService
from mizan.auth import PermissionDenied
from mizan.backup_encryption import generate_key
from mizan.bootstrap import initialize_first_run, start
from mizan.config import AppPaths
from mizan.document_store import sha256_file
from mizan.documents import DocumentService
from mizan.master import MasterDataService
from mizan.periods import PeriodService
from mizan.purchasing import PurchasingService
from mizan.quotation_export import QuotationExportService
from mizan.quotations import QuotationService
from mizan.sales import SalesService
from mizan.settings import SettingsService
from mizan.ui.audit import AuditPage
from mizan.ui.backup import BackupPage
from mizan.ui.dashboard import DashboardPage
from mizan.ui.diagnostics import DiagnosticsPage
from mizan.ui.financial_accounts import FinancialAccountDialog
from mizan.ui.login import LoginPage
from mizan.ui.main_window import MainWindow
from mizan.ui.master import MasterDataPage
from mizan.ui.purchase_settlements import (
    PurchaseReturnDialog, PurchaseSettlementHistoryDialog, SupplierPaymentDialog,
)
from mizan.ui.purchasing import PurchaseInvoiceDialog, PurchaseLineDialog, PurchasesPage
from mizan.ui.quotations import QuoteConversionDialog, QuotationDialog, QuotationLineDialog, QuotationsPage
from mizan.ui.report_catalog import REPORTS, load_report
from mizan.ui.reports import ReportsPage
from mizan.ui.sale_settlements import (
    ApplyCustomerCreditDialog, CustomerAccountDialog, CustomerReceiptDialog,
    SaleReturnDialog, SaleSettlementHistoryDialog,
)
from mizan.ui.sales import SaleInvoiceDialog, SaleLineDialog, SalesPage
from mizan.ui.settings import SettingsPage, StaffDialog


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
    page.next_button.click()
    assert page.steps.currentIndex() == 0
    page.store_name.setText("متجر النور")
    page.address.setText("القاهرة")
    page.phone.setText("01200000000")
    page.next_button.click()
    assert page.steps.currentIndex() == 1
    page.display_name.setText("المالك")
    page.username.setText("owner")
    page.password.setText("owner-pass-1")
    page.confirm.setText("owner-pass-1")

    page.submit_button.click()
    assert window.stack.currentWidget() is window.shell
    assert window.shell.nav.count() == 10
    assert set(window.shell.pages) == {
        "dashboard", "master", "purchases", "sales", "quotations", "reports", "backup", "audit", "diagnostics",
        "settings",
    }
    assert isinstance(window.shell.pages["dashboard"], DashboardPage)
    assert isinstance(window.shell.pages["master"], MasterDataPage)
    assert isinstance(window.shell.pages["purchases"], PurchasesPage)
    assert isinstance(window.shell.pages["sales"], SalesPage)
    assert isinstance(window.shell.pages["quotations"], QuotationsPage)
    assert isinstance(window.shell.pages["reports"], ReportsPage)
    assert isinstance(window.shell.pages["backup"], BackupPage)
    assert isinstance(window.shell.pages["audit"], AuditPage)
    assert isinstance(window.shell.pages["diagnostics"], DiagnosticsPage)
    assert isinstance(window.shell.pages["settings"], SettingsPage)
    assert context.user_count() == 1
    assert context.db.scalar("SELECT address FROM store_profile") == "القاهرة"
    assert context.db.scalar("SELECT phone FROM store_profile") == "01200000000"
    assert context.db.scalar("SELECT name_ar FROM financial_accounts WHERE code='CASH'") == "الصندوق الرئيسي"
    assert context.db.scalar("SELECT COUNT(*) FROM journal_entries") == 0


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
    assert window.shell.nav.count() == 10

    window.logout()
    assert window.shell is None
    assert window.stack.currentWidget() is window.login

    window.login_succeeded(cashier)
    assert window.shell.nav.count() == 5
    assert window.shell.nav.item(0).text() == "لوحة التشغيل"
    assert set(window.shell.pages) == {"dashboard", "sales", "quotations", "reports", "settings"}
    assert window.shell.pages["settings"].tabs.count() == 1


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
    assert page.tabs.count() == 6

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
    assert page.pricing is None


def test_sale_prices_can_be_saved_and_used_by_sales_drafts(qt_app, owner_context):
    context, owner = owner_context
    master = MasterDataService(context.db)
    unit = master.create_unit("PIECE", "قطعة", False)
    carton = master.create_unit("CARTON", "كرتونة", False)
    unrelated_unit = master.create_unit("METER", "متر", True)
    product = master.create_product("P-1", "مفتاح", "PIECE", "LOT")
    master.add_conversion(product, "CARTON", 12, 1)
    special = master.create_customer("C-1", "عميل الجملة", "WHOLESALE")
    retail = master.create_customer("C-2", "عميل التجزئة")

    page = MasterDataPage(context, owner)
    pricing = page.pricing
    page.tabs.setCurrentWidget(pricing)
    assert pricing.product.currentData() == product
    assert pricing.unit.findData(carton) >= 0
    assert pricing.unit.findData(unrelated_unit) == -1
    pricing.unit.setCurrentIndex(pricing.unit.findData(unit))

    pricing.price.setText("٣٠٫١٢٣٤٥٦")
    pricing.save_button.click()
    assert pricing.table.item(0, 2).text() == "30.123456"
    pricing.price.setText("30.1234567")
    assert pricing.save_price() is False
    pricing.price.setText("31")
    assert pricing.save_price() is True
    assert pricing.table.rowCount() == 1

    pricing.scope.setCurrentIndex(pricing.scope.findData("price_list_items"))
    assert not pricing.save_button.isEnabled()
    pricing.list_code.setText("TRADE")
    pricing.list_name.setText("جملة")
    pricing.list_type.setCurrentIndex(pricing.list_type.findData("WHOLESALE"))
    pricing.add_list_button.click()
    price_list = pricing.target.currentData()
    assert price_list is not None
    assert context.db.scalar(
        "SELECT list_type FROM price_lists WHERE id=?", (price_list,),
    ) == "WHOLESALE"
    pricing.price.setText("28")
    assert pricing.save_price() is True

    pricing.scope.setCurrentIndex(pricing.scope.findData("customer_prices"))
    pricing.target.setCurrentIndex(pricing.target.findData(special))
    pricing.price.setText("25.001")
    assert pricing.save_price() is True
    pricing.target.setCurrentIndex(pricing.target.findData(retail))
    assert pricing.table.rowCount() == 0

    sales = SalesService(context.db)
    lines = [{"product_id": product, "unit_id": unit, "quantity_micros": 1_000_000}]
    for customer, chosen_list, expected in (
        (special, price_list, 25_001_000),
        (retail, price_list, 28_000_000),
        (retail, None, 31_000_000),
    ):
        invoice = sales.create_invoice(
            customer, "2026-11-18", lines, price_list_id=chosen_list, actor_id=owner,
        )
        assert context.db.scalar(
            "SELECT unit_price_micros FROM sales_lines WHERE sales_id=?", (invoice,),
        ) == expected


def test_purchase_draft_can_be_corrected_and_posted_from_the_page(qt_app, owner_context, monkeypatch):
    context, owner = owner_context
    master = MasterDataService(context.db)
    unit = master.create_unit("PIECE", "قطعة", False)
    carton = master.create_unit("CARTON", "كرتونة", False)
    product = master.create_product("P-1", "مفتاح", "PIECE", "LOT")
    master.add_conversion(product, "CARTON", 12, 1)
    supplier = master.create_supplier("S-1", "المورد")
    manager = context.auth.create_user("manager", "المدير", "manager-pass", ["MANAGER"])
    window = MainWindow(context)
    window.login_succeeded(manager)
    page = window.shell.pages["purchases"]
    assert isinstance(page, PurchasesPage)

    line = PurchaseLineDialog(context)
    line.product.setCurrentIndex(line.product.findData(product))
    assert line.unit.currentData() == unit
    line.unit.setCurrentIndex(line.unit.findData(carton))
    line.quantity.setText("٢")
    line.price.setText("١٢٠٫٥٠")
    line.save_button.click()
    assert line.line is not None

    editor = PurchaseInvoiceDialog(context, manager)
    editor.supplier.setCurrentIndex(editor.supplier.findData(supplier))
    editor.invoice_date.setDate(QDate(2026, 11, 9))
    editor.has_due.setChecked(True)
    editor.due_date.setDate(QDate(2026, 12, 1))
    editor.supplier_reference.setText("INV-7")
    editor.expense.setText("5.25")
    editor.lines.append(line.line)
    editor.save_button.click()
    invoice = editor.invoice_id
    assert invoice is not None
    assert context.db.scalar("SELECT COUNT(*) FROM purchase_lots") == 0
    assert context.db.scalar("SELECT COUNT(*) FROM journal_entries") == 0
    assert context.db.scalar("SELECT due_date FROM purchase_invoices WHERE id=?", (invoice,)) == "2026-12-01"
    assert context.db.scalar("SELECT base_qty_micros FROM purchase_invoice_lines") == 24_000_000

    original_number = context.db.scalar("SELECT document_no FROM purchase_invoices WHERE id=?", (invoice,))
    persisted = PurchaseInvoiceDialog(context, manager, invoice_id=invoice)
    bad_line = persisted.lines[0].values() | {"quantity_micros": 0}
    with pytest.raises(ValueError, match="quantity must be positive"):
        PurchasingService(context.db).update_invoice(
            invoice, supplier, "2026-11-09", [bad_line], expense_piastres=800, actor_id=manager,
        )
    assert context.db.scalar("SELECT expense_piastres FROM purchase_invoices WHERE id=?", (invoice,)) == 525
    assert context.db.scalar("SELECT base_qty_micros FROM purchase_invoice_lines") == 24_000_000

    update = PurchaseLineDialog(context, line=persisted.lines[0])
    update.quantity.setText("3")
    assert update.save()
    persisted.lines[0] = update.line
    assert persisted.save()
    assert context.db.scalar("SELECT document_no FROM purchase_invoices WHERE id=?", (invoice,)) == original_number
    assert context.db.scalar("SELECT base_qty_micros FROM purchase_invoice_lines") == 36_000_000

    page.refresh(invoice)
    assert page.post_button.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    page.post_button.click()
    assert context.db.scalar("SELECT status FROM purchase_invoices WHERE id=?", (invoice,)) == "DRAFT"

    periods = PeriodService(context.db)
    periods.close_month("2026-11", actor_id=owner, reason="اختبار")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    page.post_button.click()
    assert context.db.scalar("SELECT status FROM purchase_invoices WHERE id=?", (invoice,)) == "DRAFT"
    assert context.db.scalar("SELECT COUNT(*) FROM purchase_lots") == 0
    assert context.db.scalar("SELECT COUNT(*) FROM journal_entries") == 0
    periods.reopen_month("2026-11", actor_id=owner, reason="اختبار إعادة المحاولة")

    page.status.setCurrentIndex(page.status.findData("DRAFT"))
    page.refresh(invoice)
    page.post_button.click()
    assert page.status.currentData() == "POSTED"
    assert not page.post_button.isEnabled()
    assert not page.post_invoice(invoice)
    assert context.db.scalar("SELECT status FROM purchase_invoices WHERE id=?", (invoice,)) == "POSTED"
    assert context.db.scalar("SELECT COUNT(*) FROM purchase_lots") == 1
    assert context.db.scalar("SELECT COUNT(*) FROM journal_entries") == 1
    assert context.accounting.account_balance("1200") == 36_675
    assert context.accounting.account_balance("2010") == -36_150
    assert context.accounting.account_balance("2030") == -525
    assert PurchaseInvoiceDialog(context, manager, invoice_id=invoice).read_only


def test_cash_purchase_creates_reels_and_posts_to_the_configured_account(qt_app, owner_context):
    context, owner = owner_context
    master = MasterDataService(context.db)
    master.create_unit("METER", "متر", True)
    product = master.create_product("CABLE", "كابل", "METER", "REEL")
    supplier = master.create_supplier("S-1", "المورد")
    manager = context.auth.create_user("manager", "المدير", "manager-pass", ["MANAGER"])
    account_setup = FinancialAccountDialog(context, manager)
    account_setup.code.setText("CASH")
    account_setup.name.setText("الخزنة")
    assert not account_setup.save()
    account_setup = FinancialAccountDialog(context, owner)
    account_setup.code.setText("CASH")
    account_setup.name.setText("الخزنة")
    assert account_setup.save()

    line = PurchaseLineDialog(context)
    line.product.setCurrentIndex(line.product.findData(product))
    line.quantity.setText("7.25")
    line.price.setText("10")
    line.lengths.setText("2.25، 4")
    assert not line.save()
    line.lengths.setText("٢٫٢٥، ٥")
    assert line.save()

    editor = PurchaseInvoiceDialog(context, manager)
    editor.supplier.setCurrentIndex(editor.supplier.findData(supplier))
    editor.invoice_date.setDate(QDate(2026, 11, 9))
    editor.payment.setCurrentIndex(editor.payment.findData("CASH"))
    editor.lines.append(line.line)
    assert not editor.save()
    editor.account.setCurrentIndex(editor.account.findData(account_setup.account_id))
    assert editor.save()
    persisted = PurchaseInvoiceDialog(context, manager, invoice_id=editor.invoice_id)
    assert persisted.lines[0].reel_lengths_micros == (2_250_000, 5_000_000)
    edit_line = PurchaseLineDialog(context, line=persisted.lines[0])
    edit_line.lengths.setText("3، 4.25")
    assert edit_line.save()
    persisted.lines[0] = edit_line.line
    assert persisted.save()

    assert PurchasesPage(context, manager).post_invoice(editor.invoice_id)
    assert context.db.scalar("SELECT COUNT(*) FROM reels") == 2
    assert context.db.scalar("SELECT SUM(original_length_micros) FROM reels") == 7_250_000
    assert context.db.scalar("SELECT MIN(original_length_micros) FROM reels") == 3_000_000
    assert context.accounting.account_balance("1010") == -7_250
    assert context.accounting.account_balance("2010") == 0
    with pytest.raises(ValueError, match="draft purchase invoice"):
        PurchasingService(context.db).update_invoice(
            editor.invoice_id, supplier, "2026-11-09", [line.line.values()], actor_id=manager,
        )


def test_supplier_payment_allocates_to_two_invoices_from_purchases(qt_app, owner_context, monkeypatch):
    context, owner = owner_context
    master = MasterDataService(context.db)
    unit = master.create_unit("UNIT", "قطعة", False)
    product = master.create_product("LOT", "صنف", "UNIT", "LOT")
    supplier = master.create_supplier("S", "مورد")
    account = context.accounting.create_financial_account("BANK", "البنك", "BANK", "1020")
    service = PurchasingService(context.db)
    lines = [{"product_id": product, "unit_id": unit, "quantity_micros": 1_000_000,
              "unit_price_micros": 100_000_000}]
    invoices = [service.create_invoice(supplier, "2026-11-10", lines) for _ in range(2)]
    for invoice in invoices:
        service.post_invoice(invoice, actor_id=owner)
    page = PurchasesPage(context, owner)
    page.refresh(invoices[0])

    def enter_payment(dialog):
        assert dialog.supplier.currentData() == supplier
        dialog.date.setDate(QDate(2026, 11, 11))
        dialog.account.setCurrentIndex(dialog.account.findData(account))
        dialog.reference.setText("BANK-7")
        assert len(dialog.amount_fields) == 2
        dialog.amount_fields[0].setText("100.01")
        assert not dialog.save()
        dialog.amount_fields[0].setText("١٠٠")
        dialog.amount_fields[1].setText("25.50")
        assert dialog.save()
        return dialog.result()

    monkeypatch.setattr(SupplierPaymentDialog, "exec", enter_payment)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    page.pay_button.click()
    assert "SP-" in page.message.text()
    assert context.db.scalar("SELECT COUNT(*) FROM supplier_payment_allocations") == 2
    assert context.accounting.account_balance("1020") == -12_550
    assert context.accounting.account_balance("2010") == -7_450
    assert [(row["purchase_invoice_id"], row["outstanding_piastres"])
            for row in service.supplier_payment_candidates(supplier, "2026-11-10")] == [(invoices[1], 7_450)]
    history = PurchaseSettlementHistoryDialog(context, owner, invoices[1])
    assert history.table.rowCount() == 1
    assert history.table.item(0, 3).text() == "BANK-7"
    assert history.table.item(0, 4).text() == "25.50 EGP"


def test_purchase_return_uses_only_original_stock_and_rolls_back_closed_period(qt_app, owner_context, monkeypatch):
    context, owner = owner_context
    master = MasterDataService(context.db)
    unit = master.create_unit("METER", "متر", True)
    product = master.create_product("CABLE", "كابل", "METER", "REEL")
    supplier = master.create_supplier("S", "مورد")
    service = PurchasingService(context.db)
    lines = [{"product_id": product, "unit_id": unit, "quantity_micros": 10_000_000,
              "unit_price_micros": 10_000_000, "reel_lengths_micros": [4_000_000, 6_000_000]}]
    invoice, other = [service.create_invoice(supplier, "2026-11-10", lines) for _ in range(2)]
    for invoice_id in (invoice, other):
        service.post_invoice(invoice_id, actor_id=owner)
    foreign_line = context.db.scalar(
        "SELECT id FROM purchase_invoice_lines WHERE purchase_invoice_id=?", (other,),
    )
    foreign_lot = context.db.scalar("SELECT id FROM purchase_lots WHERE purchase_invoice_line_id=?", (foreign_line,))
    foreign_reel = context.db.scalar("SELECT MIN(id) FROM reels WHERE lot_id=?", (foreign_lot,))
    with pytest.raises(ValueError, match="original invoice"):
        service.return_credit_purchase(
            invoice, [{"purchase_invoice_line_id": foreign_line, "lot_id": foreign_lot,
                       "reel_id": foreign_reel, "quantity_micros": 1_000_000}],
            "2026-11-11", reason="اختبار", actor_id=owner,
        )
    assert context.db.scalar("SELECT COUNT(*) FROM purchase_returns") == 0
    periods = PeriodService(context.db)
    periods.close_month("2026-11", actor_id=owner, reason="اختبار")
    page = PurchasesPage(context, owner)
    page.refresh(invoice)
    assert page.return_button.isEnabled()

    def enter_return(dialog):
        assert len(dialog.sources) == 2
        dialog.date.setDate(QDate(2026, 11, 11))
        dialog.reason.setText("مرتجع")
        dialog.quantity_fields[0].setText("5")
        assert not dialog.save()
        dialog.quantity_fields[0].setText("٤")
        assert not dialog.save()
        assert context.db.scalar("SELECT COUNT(*) FROM purchase_returns") == 0
        assert service.reel_remaining(dialog.sources[0][2])[0] == 4_000_000
        periods.reopen_month("2026-11", actor_id=owner, reason="اختبار")
        assert dialog.save()
        return dialog.result()

    monkeypatch.setattr(PurchaseReturnDialog, "exec", enter_return)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    page.return_button.click()
    assert "PR-" in page.message.text()
    assert context.db.scalar("SELECT purchase_invoice_id FROM purchase_returns") == invoice
    assert context.db.scalar("SELECT COUNT(*) FROM reels WHERE status='EMPTY'") == 1
    assert context.accounting.account_balance("2010") == -16_000
    assert context.accounting.reconcile_inventory()["matched"]
    history = PurchaseSettlementHistoryDialog(context, owner, invoice)
    assert history.table.rowCount() == 1
    assert history.table.item(0, 0).text().startswith("PR-")


def test_cashier_sale_uses_customer_price_and_posts_only_when_stock_is_available(qt_app, owner_context, monkeypatch):
    context, owner = owner_context
    master = MasterDataService(context.db)
    unit = master.create_unit("UNIT", "قطعة", False)
    product = master.create_product("P", "مفتاح", "UNIT", "LOT")
    customer = master.create_customer("C", "عميل")
    supplier = master.create_supplier("S", "مورد")
    price_list = master.create_price_list("LIST", "جملة")
    price_key = {"product_id": product, "unit_id": unit}
    master.set_price("product_default_prices", **price_key, price_micros=30_000_000)
    master.set_price("price_list_items", **price_key, price_list_id=price_list, price_micros=28_000_000)
    master.set_price("customer_prices", **price_key, customer_id=customer, price_micros=25_000_000)
    cashier = context.auth.create_user("cashier", "الكاشير", "cashier-pass", ["CASHIER"])
    window = MainWindow(context)
    window.login_succeeded(cashier)
    page = window.shell.pages["sales"]

    def enter_line(dialog):
        dialog.product.setCurrentIndex(dialog.product.findData(product))
        dialog.quantity.setText("٢")
        dialog.discount.setText("١٫٥٠")
        assert not dialog.manual.isEnabled()
        assert dialog.save()
        return dialog.result()

    def create_draft(dialog):
        dialog.customer.setCurrentIndex(dialog.customer.findData(customer))
        dialog.price_list.setCurrentIndex(dialog.price_list.findData(price_list))
        dialog.invoice_date.setDate(QDate(2026, 11, 11))
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "إضافة بند").click()
        assert dialog.table.item(0, 4).text() == "سعر العميل"
        assert dialog.save()
        return dialog.result()

    monkeypatch.setattr(SaleLineDialog, "exec", enter_line)
    monkeypatch.setattr(SaleInvoiceDialog, "exec", create_draft)
    page.create_button.click()
    invoice = context.db.scalar("SELECT id FROM sales")
    assert page.post_button.isEnabled()
    assert page.cancel_button.isHidden()
    assert not page.post_invoice(invoice)
    assert context.db.scalar("SELECT status FROM sales WHERE id=?", (invoice,)) == "DRAFT"
    assert context.db.scalar("SELECT COUNT(*) FROM journal_entries") == 0

    master.set_price("customer_prices", **price_key, customer_id=customer, price_micros=35_000_000, update_existing=True)
    editor = SaleInvoiceDialog(context, cashier, invoice_id=invoice)
    editor.has_due.setChecked(True)
    editor.due_date.setDate(QDate(2026, 12, 1))
    assert editor.save()
    assert context.db.scalar("SELECT unit_price_micros FROM sales_lines WHERE sales_id=?", (invoice,)) == 25_000_000
    assert context.db.scalar("SELECT due_date FROM sales WHERE id=?", (invoice,)) == "2026-12-01"

    purchase = PurchasingService(context.db)
    received = purchase.create_invoice(supplier, "2026-11-10", [
        price_key | {"quantity_micros": 3_000_000, "unit_price_micros": 18_000_000},
    ])
    purchase.post_invoice(received, actor_id=owner)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    page.post_button.click()
    assert context.db.scalar("SELECT status FROM sales WHERE id=?", (invoice,)) == "POSTED"
    assert context.accounting.account_balance("1100") == 4_850
    assert context.accounting.account_balance("1200") == 1_800
    assert context.accounting.reconcile_inventory()["matched"]
    assert SaleInvoiceDialog(context, cashier, invoice_id=invoice).read_only


def test_cash_sale_needs_below_cost_approval_after_clearing_a_price_list(qt_app, owner_context, monkeypatch):
    context, owner = owner_context
    master = MasterDataService(context.db)
    unit = master.create_unit("M", "متر", True)
    product = master.create_product("P", "كابل", "M", "REEL")
    customer = master.create_customer("C", "مقاول", "CONTRACTOR")
    project = master.create_project(customer, "SITE", "مشروع")
    supplier = master.create_supplier("S", "مورد")
    account = context.accounting.create_financial_account("CASH", "الخزنة", "CASH", "1010")
    price_list = master.create_price_list("LIST", "أسعار")
    price_key = {"product_id": product, "unit_id": unit}
    master.set_price("product_default_prices", **price_key, price_micros=10_000_000)
    master.set_price("price_list_items", **price_key, price_list_id=price_list, price_micros=30_000_000)
    purchase = PurchasingService(context.db)
    received = purchase.create_invoice(supplier, "2026-11-10", [
        price_key | {"quantity_micros": 5_000_000, "unit_price_micros": 20_000_000,
                     "reel_lengths_micros": [5_000_000]},
    ])
    purchase.post_invoice(received, actor_id=owner)

    service = SalesService(context.db)
    invoice = service.create_invoice(
        customer, "2026-11-11", [price_key | {"quantity_micros": 2_000_000}],
        price_list_id=price_list, project_id=project, actor_id=owner,
    )
    editor = SaleInvoiceDialog(context, owner, invoice_id=invoice)
    editor.price_list.setCurrentIndex(0)
    editor.payment.setCurrentIndex(editor.payment.findData("CASH"))
    editor.account.setCurrentIndex(editor.account.findData(account))
    editor.discount.setText("2")
    assert editor.save()
    assert context.db.scalar("SELECT price_list_id FROM sales WHERE id=?", (invoice,)) is None
    assert context.db.scalar("SELECT project_id FROM sales WHERE id=?", (invoice,)) == project
    assert context.db.scalar("SELECT total_piastres FROM sales WHERE id=?", (invoice,)) == 1_800

    cashier = context.auth.create_user("cashier", "الكاشير", "cashier-pass", ["CASHIER"])
    assert not SalesPage(context, cashier).post_invoice(invoice)
    assert context.db.scalar("SELECT COUNT(*) FROM journal_entries WHERE source_type='SALES_INVOICE'") == 0
    assert context.db.scalar("SELECT SUM(remaining_qty_micros) FROM inventory_cost_layers") == 5_000_000
    page = SalesPage(context, owner)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("", False))
    assert not page.post_invoice(invoice)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("تصفية المخزون", True))
    assert page.post_invoice(invoice)
    assert context.db.scalar("SELECT below_cost_approved_by FROM sales WHERE id=?", (invoice,)) == owner
    assert context.accounting.account_balance("1010") == 1_800
    assert context.db.scalar("SELECT SUM(remaining_qty_micros) FROM inventory_cost_layers") == 3_000_000
    assert context.accounting.reconcile_inventory()["matched"]


def test_cashier_collection_allocates_invoices_and_records_excess_credit(qt_app, owner_context, monkeypatch):
    context, owner = owner_context
    master = MasterDataService(context.db)
    unit = master.create_unit("PCS", "قطعة", False)
    product = master.create_product("P", "مفتاح", "PCS", "LOT")
    customer = master.create_customer("C", "عميل")
    supplier = master.create_supplier("S", "مورد")
    account = context.accounting.create_financial_account("BANK", "البنك", "BANK", "1020")
    line = {"product_id": product, "unit_id": unit, "quantity_micros": 1_000_000,
            "unit_price_micros": 50_000_000}
    purchasing = PurchasingService(context.db)
    receipt = purchasing.create_invoice(
        supplier, "2026-11-09", [line | {"quantity_micros": 3_000_000, "unit_price_micros": 20_000_000}],
    )
    purchasing.post_invoice(receipt, actor_id=owner)
    sales = SalesService(context.db)
    invoices = [sales.create_invoice(customer, "2026-11-10", [line], actor_id=owner) for _ in range(2)]
    for invoice in invoices:
        sales.post_invoice(invoice, actor_id=owner)
    cashier = context.auth.create_user("cashier", "الكاشير", "cashier-pass", ["CASHIER"])
    page = SalesPage(context, cashier)
    page.refresh(invoices[0])
    assert page.return_button.isHidden()
    periods = PeriodService(context.db)
    periods.close_month("2026-11", actor_id=owner, reason="اختبار")

    def enter_receipt(dialog):
        assert dialog.customer.currentData() == customer
        dialog.account.setCurrentIndex(dialog.account.findData(account))
        dialog.date.setDate(QDate(2026, 11, 9))
        assert not dialog.invoices
        dialog.date.setDate(QDate(2026, 11, 11))
        dialog.amount.setText("١٢٠")
        dialog.fields[0].setText("50")
        dialog.fields[1].setText("45")
        assert not dialog.save()
        assert context.db.scalar("SELECT COUNT(*) FROM customer_receipts") == 0
        periods.reopen_month("2026-11", actor_id=owner, reason="اختبار")
        assert dialog.save()
        return dialog.result()

    monkeypatch.setattr(CustomerReceiptDialog, "exec", enter_receipt)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    page.receipt_button.click()
    assert "CR-" in page.message.text()
    assert context.db.scalar("SELECT COUNT(*) FROM customer_receipt_allocations") == 2
    assert sales.invoice_balance(invoices[1]) == 500
    assert context.accounting.account_balance("1020") == 12_000
    assert sales.customer_credit_balance(customer) == 2_500
    statement = CustomerAccountDialog(context, cashier, customer_id=customer)
    assert statement.table.rowCount() == 4
    assert statement.table.item(3, 5).text() == "5.00 EGP"
    assert statement.table.item(3, 6).text() == "25.00 EGP"
    page.refresh(invoices[1])

    def apply_credit(dialog):
        assert dialog.outstanding == 500
        dialog.source.setCurrentIndex(0)
        dialog.amount.setText("5")
        dialog.date.setDate(QDate(2026, 11, 10))
        assert not dialog.save()
        assert sales.invoice_balance(invoices[1]) == 500
        dialog.date.setDate(QDate(2026, 11, 12))
        assert dialog.save()
        return dialog.result()

    monkeypatch.setattr(ApplyCustomerCreditDialog, "exec", apply_credit)
    page.credit_button.click()
    assert sales.invoice_balance(invoices[1]) == 0
    assert sales.customer_credit_balance(customer) == 2_000
    history = SaleSettlementHistoryDialog(context, cashier, invoices[1])
    assert history.table.rowCount() == 2
    assert history.table.item(0, 1).text() == "استخدام رصيد دائن"


def test_sales_return_uses_original_reels_and_settles_cash_and_credit(qt_app, owner_context, monkeypatch):
    context, owner = owner_context
    master = MasterDataService(context.db)
    unit = master.create_unit("M", "متر", True)
    product = master.create_product("P", "كابل", "M", "REEL")
    customer = master.create_customer("C", "عميل")
    supplier = master.create_supplier("S", "مورد")
    account = context.accounting.create_financial_account("CASH", "الخزنة", "CASH", "1010")
    purchasing = PurchasingService(context.db)
    receipt = purchasing.create_invoice(supplier, "2026-11-09", [{
        "product_id": product, "unit_id": unit, "quantity_micros": 10_000_000,
        "unit_price_micros": 10_000_000, "reel_lengths_micros": [3_000_000, 7_000_000],
    }])
    purchasing.post_invoice(receipt, actor_id=owner)
    sales = SalesService(context.db)
    invoice = sales.create_invoice(customer, "2026-11-10", [{
        "product_id": product, "unit_id": unit, "quantity_micros": 6_000_000, "unit_price_micros": 20_000_000,
    }], payment_type="CASH", financial_account_id=account, actor_id=owner)
    sales.post_invoice(invoice, actor_id=owner)
    master.deactivate_product(product)
    page = SalesPage(context, owner)
    page.refresh(invoice)

    def enter_return(dialog):
        assert len(dialog.sources) == 2
        assert dialog.sources[0]["source"] != dialog.sources[1]["source"]
        dialog.date.setDate(QDate(2026, 11, 11))
        dialog.reason.setText("اختبار تسوية المرتجع")
        dialog.fields[0].setText("1")
        dialog.fields[1].setText("1")
        dialog.conditions[1].setCurrentIndex(dialog.conditions[1].findData("DAMAGED"))
        assert "40.00 EGP" in dialog.summary.text()
        dialog.method.setCurrentIndex(dialog.method.findData("MIXED"))
        dialog.account.setCurrentIndex(dialog.account.findData(account))
        dialog.reference.setText("RF-1")
        dialog.refund.setText("41")
        assert not dialog.save()
        assert context.db.scalar("SELECT COUNT(*) FROM sales_returns") == 0
        assert context.accounting.account_balance("1010") == 12_000
        dialog.refund.setText("20")
        assert dialog.save()
        return dialog.result()

    monkeypatch.setattr(SaleReturnDialog, "exec", enter_return)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    page.return_button.click()
    assert "SR-" in page.message.text()
    assert context.db.scalar("SELECT sales_id FROM sales_returns") == invoice
    assert context.db.scalar("SELECT COUNT(*) FROM sales_return_line_allocations") == 2
    assert sales.customer_credit_balance(customer) == 2_000
    assert context.accounting.account_balance("1010") == 10_000
    assert context.accounting.account_balance("5020") == 1_000
    assert context.accounting.reconcile_inventory()["matched"]
    history = SaleSettlementHistoryDialog(context, owner, invoice)
    assert history.table.item(0, 0).text().startswith("SR-")


def test_quotation_page_issues_verified_pdf_and_opens_linked_sale(qt_app, owner_context, monkeypatch):
    context, owner = owner_context
    master = MasterDataService(context.db)
    unit = master.create_unit("UNIT", "قطعة", False)
    product = master.create_product("P", "مفتاح", "UNIT", "LOT")
    customer = master.create_customer("C", "عميل")
    price_key = {"product_id": product, "unit_id": unit}
    master.set_price("product_default_prices", **price_key, price_micros=24_000_000)
    window = MainWindow(context)
    window.login_succeeded(owner)
    page = window.shell.pages["quotations"]

    def add_line(dialog):
        dialog.product.setCurrentIndex(dialog.product.findData(product))
        dialog.quantity.setText("٢")
        dialog.description.setText("مفتاح إنارة خاص")
        assert dialog.save()
        return dialog.result()

    def create(dialog):
        dialog.customer.setCurrentIndex(dialog.customer.findData(customer))
        dialog.add_line_button.click()
        dialog.notes.setPlainText("ملاحظة العرض")
        dialog.terms.setPlainText("التسليم من المتجر")
        assert dialog.save()
        return dialog.result()

    monkeypatch.setattr(QuotationLineDialog, "exec", add_line)
    monkeypatch.setattr(QuotationDialog, "exec", create)
    window.shell.nav.setCurrentRow(window.shell.content.indexOf(page))
    page.create_button.click()
    quote_id = context.db.scalar("SELECT id FROM quotations")
    assert context.db.scalar("SELECT description_snapshot FROM quotation_lines") == "مفتاح إنارة خاص"
    assert page.issue_button.isEnabled()

    cashier = context.auth.create_user("cashier", "الكاشير", "cashier-pass", ["CASHIER"])
    assert not QuotationsPage(context, cashier).issue(quote_id)
    assert context.db.scalar("SELECT COUNT(*) FROM document_exports") == 0
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    page.issue_button.click()
    assert context.db.scalar("SELECT status FROM quotations") == "ISSUED"
    pdf = QuotationExportService(context.db, documents_root=context.paths.documents_root).reprint_pdf(quote_id)
    assert pdf["path"].read_bytes().startswith(b"%PDF")
    assert sha256_file(pdf["path"]) == pdf["sha256"]
    opened = []

    def capture_pdf(url):
        opened.append(url.toLocalFile())
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", capture_pdf)
    page.pdf_button.click()
    assert opened == [pdf["path"].as_posix()]

    printed = context.paths.root / "quotation-print-proof.pdf"

    class PrintToPdfDialog:
        def __init__(self, printer, parent):
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(str(printed))

        def exec(self):
            return QuotationDialog.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr("mizan.ui.quotations.QPrintDialog", PrintToPdfDialog)
    page.print_action.trigger()
    assert printed.read_bytes().startswith(b"%PDF")
    assert sha256_file(pdf["path"]) == pdf["sha256"]

    master.set_price("product_default_prices", **price_key, price_micros=50_000_000, update_existing=True)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("موافقة", True))
    page.accept_action.trigger()
    assert context.db.scalar("SELECT status FROM quotations") == "ACCEPTED"
    assert not page.cancel_action.isEnabled()

    def convert(dialog):
        assert dialog.save()
        return dialog.result()

    monkeypatch.setattr(QuoteConversionDialog, "exec", convert)
    page.convert_button.click()
    sale_id = context.db.scalar("SELECT id FROM sales WHERE source_quotation_id=?", (quote_id,))
    assert sale_id is not None
    assert context.db.scalar("SELECT status FROM sales") == "DRAFT"
    assert context.db.scalar("SELECT total_piastres FROM sales") == 4_800
    assert context.db.scalar("SELECT COUNT(*) FROM journal_entries") == 0
    assert "نقص استرشادي" in page.message.text()
    assert not page.convert_button.isEnabled()

    opened_sale = []

    def inspect_sale(dialog):
        opened_sale.append((dialog.invoice_id, dialog.locked))
        return dialog.Rejected

    monkeypatch.setattr(SaleInvoiceDialog, "exec", inspect_sale)
    page.sale_button.click()
    assert window.shell.content.currentWidget() is window.shell.pages["sales"]
    assert opened_sale == [(sale_id, True)]


def test_quotation_revision_holds_original_price_until_commercial_edits(qt_app, owner_context, monkeypatch):
    context, owner = owner_context
    master = MasterDataService(context.db)
    unit = master.create_unit("UNIT", "قطعة", False)
    product = master.create_product("P", "مفتاح", "UNIT", "LOT")
    customer = master.create_customer("C", "عميل")
    price_key = {"product_id": product, "unit_id": unit}
    master.set_price("product_default_prices", **price_key, price_micros=20_000_000)
    service = QuotationService(context.db)
    first = service.create_quotation(
        customer, "2026-01-01", [price_key | {"quantity_micros": 1_000_000}],
        valid_until="2026-01-02", actor_id=owner,
    )
    page = QuotationsPage(context, owner)
    assert page.issue(first)
    official = page.exports.reprint_pdf(first)
    assert page.table.item(0, 4).text() == "منتهي"
    expired = QuoteConversionDialog(context, owner, first)
    assert not expired.save()
    assert context.db.scalar("SELECT COUNT(*) FROM sales") == 0

    master.set_price("product_default_prices", **price_key, price_micros=30_000_000, update_existing=True)

    def edit_revision(dialog):
        dialog.has_expiry.setChecked(False)
        dialog.terms.setPlainText("شروط المراجعة")
        assert dialog.save()
        return dialog.result()

    monkeypatch.setattr(QuotationDialog, "exec", edit_revision)
    page.revise_button.click()
    revision = context.db.scalar("SELECT id FROM quotations WHERE version_no=2")
    assert revision is not None
    assert service.get_quotation(first)["status"] == "ISSUED"
    assert service.get_quotation(revision)["lines"][0]["final_unit_price_micros"] == 20_000_000
    assert page.issue(revision)
    assert service.get_quotation(first)["status"] == "SUPERSEDED"
    assert service.get_quotation(revision)["terms"] == "شروط المراجعة"
    assert page.exports.reprint_pdf(first)["sha256"] == official["sha256"]


@pytest.fixture()
def report_shop(owner_context):
    context, owner = owner_context
    master = MasterDataService(context.db)
    unit = master.create_unit("M", "متر", True)
    product = master.create_product("P", "كابل", "M", "REEL")
    other_product = master.create_product("P2", "صنف بلا حركات", "M", "LOT")
    customer = master.create_customer("C", "مقاول", "CONTRACTOR")
    project = master.create_project(customer, "SITE", "مشروع")
    supplier = master.create_supplier("S", "مورد")
    account = context.accounting.create_financial_account("BANK", "البنك", "BANK", "1020")
    cashier = context.auth.create_user("cashier", "الكاشير", "cashier-pass", ["CASHIER"])
    purchasing = PurchasingService(context.db)
    purchase = purchasing.create_invoice(supplier, "2026-11-15", [{
        "product_id": product, "unit_id": unit, "quantity_micros": 10_000_000,
        "unit_price_micros": 20_000_000, "reel_lengths_micros": [4_000_000, 6_000_000],
    }])
    purchasing.post_invoice(purchase, actor_id=owner)
    sales = SalesService(context.db)
    payload = {"product_id": product, "unit_id": unit, "unit_price_micros": 40_000_000}
    owner_sale = sales.create_invoice(
        customer, "2026-11-17", [payload | {"quantity_micros": 2_000_000}], project_id=project, actor_id=owner,
    )
    cashier_sale = sales.create_invoice(
        customer, "2026-11-17", [payload | {"quantity_micros": 3_000_000}], project_id=project, actor_id=cashier,
    )
    sales.post_invoice(owner_sale, actor_id=owner)
    sales.post_invoice(cashier_sale, actor_id=cashier)
    line = context.db.scalar("SELECT id FROM sales_lines WHERE sales_id=?", (owner_sale,))
    sales.record_sales_return(
        owner_sale, [{"sales_line_id": line, "quantity_micros": 1_250_000}],
        "2026-11-18", "اختبار", actor_id=owner,
    )
    sales.record_customer_receipt(
        customer, account, 1_700, "2026-11-18", [{"sales_id": owner_sale, "amount_piastres": 1_500}], actor_id=owner,
    )
    return context, owner, cashier, customer, supplier, other_product, cashier_sale


def test_desktop_reports_show_period_details_and_match_the_ledger(qt_app, report_shop):
    context, owner, _, customer, supplier, other_product, _ = report_shop
    page = ReportsPage(context, owner)
    page.from_date.setDate(QDate(2026, 11, 1))
    page.through_date.setDate(QDate(2026, 11, 18))
    assert page.report.count() == len(REPORTS)
    assert len(REPORTS) == 17

    for key, spec in REPORTS.items():
        page.report.setCurrentIndex(page.report.findData(key))
        if spec.required_filter:
            assert page.run() is None
            field = page.filters[spec.required_filter]
            field.setCurrentIndex(field.findData(customer if key == "customer_statement" else supplier))
        job = page.run()
        state = job.wait(30)
        assert state.status == "succeeded", (key, state.error)
        page._poll()
        assert page.results.count() == len(spec.tables)
        assert page.results.widget(0).model().rowCount() > 0, key
        if key == "sales":
            model = page.results.widget(0).model()
            assert model.rowCount() == 3
            assert model.data(model.index(2, 6)) == "-1.25"
            assert "150.00 EGP" in page.totals.text()
        elif key == "valuation":
            assert "125.00 EGP" in page.totals.text()
            assert "0.00 EGP" in page.totals.text()
        elif key == "profit":
            assert "75.00 EGP" in page.totals.text()

    page.report.setCurrentIndex(page.report.findData("sales"))
    product_filter = page.filters["product_id"]
    product_filter.setCurrentIndex(product_filter.findData(other_product))
    assert page.run().wait(30).status == "succeeded"
    page._poll()
    assert page.results.widget(0).model().rowCount() == 0
    page.from_date.setDate(QDate(2026, 11, 30))
    assert page.run() is None
    assert page.results.count() == 0


def test_report_permissions_limit_cashier_activity_and_remove_cost_fields(qt_app, report_shop):
    context, owner, cashier, _, _, _, cashier_sale = report_shop
    cashier_page = ReportsPage(context, cashier)
    assert {cashier_page.report.itemData(index) for index in range(cashier_page.report.count())} == {
        "sales", "customer_statement",
    }
    assert "posted_by" not in cashier_page.filters
    sales, _ = load_report(context, cashier, "sales", "2026-11-01", "2026-11-18", {"posted_by": owner})
    invoice_no = context.db.scalar("SELECT document_no FROM sales WHERE id=?", (cashier_sale,))
    assert [row["document_no"] for row in sales["lines"]] == [invoice_no]
    assert sales["net_sales_piastres"] == 12_000
    assert "cost_of_sales_piastres" not in sales["lines"][0]
    with pytest.raises(PermissionDenied):
        load_report(context, cashier, "profit", "2026-11-01", "2026-11-18", {})

    with context.db.transaction() as conn:
        role = conn.execute("INSERT INTO roles(code,name_ar) VALUES('STOCK_CLERK','مخزن')").lastrowid
        conn.execute(
            "INSERT INTO role_permissions(role_id,permission_id) SELECT ?,id FROM permissions "
            "WHERE code IN ('manage_inventory','view_purchase_reports')", (role,),
        )
    stock_clerk = context.auth.create_user("stock", "موظف المخزن", "stock-pass", ["STOCK_CLERK"])
    purchases, _ = load_report(context, stock_clerk, "purchases", "2026-11-01", "2026-11-18", {})
    reels, _ = load_report(context, stock_clerk, "reels", "", "", {})
    assert purchases["lines"][0]["quantity_micros"] == 10_000_000
    assert "supplier_piastres" not in purchases["lines"][0]
    assert "inventory_piastres" not in purchases["lines"][0]
    assert "remaining_cost_piastres" not in reels["rows"][0]
    with pytest.raises(PermissionDenied):
        load_report(context, stock_clerk, "valuation", "", "2026-11-18", {})
    stock_page = ReportsPage(context, stock_clerk)
    stock_page.report.setCurrentIndex(stock_page.report.findData("purchases"))
    stock_page.from_date.setDate(QDate(2026, 11, 1))
    stock_page.through_date.setDate(QDate(2026, 11, 18))
    assert stock_page.run().wait(30).status == "succeeded"
    with context.db.transaction() as conn:
        conn.execute(
            "DELETE FROM role_permissions WHERE role_id=? AND permission_id="
            "(SELECT id FROM permissions WHERE code='view_purchase_reports')", (role,),
        )
    stock_page._poll()
    assert stock_page.results.count() == 0
    assert "تغيّرت الصلاحيات" in stock_page.message.text()


def test_store_financial_accounts_and_periods_settings(qt_app, owner_context, monkeypatch):
    context, owner = owner_context
    page = SettingsPage(context, owner)
    assert page.tabs.count() == 5
    assert page.store.name.text() == "متجر النور"
    page.store.name.setText("متجر القاهرة")
    page.store.address.setText("وسط البلد")
    page.store.phone.setText("01200000000")
    page.store.save_button.click()
    assert context.db.scalar("SELECT address FROM store_profile") == "وسط البلد"
    assert context.db.scalar("SELECT phone FROM store_profile") == "01200000000"
    assert context.db.scalar("SELECT COUNT(*) FROM audit_events WHERE event_type='STORE_PROFILE_UPDATED'") == 1

    def create_account(dialog):
        dialog.code.setText("BANK")
        dialog.name.setText("حساب البنك")
        dialog.kind.setCurrentIndex(1)
        assert dialog.save()
        return dialog.result()

    monkeypatch.setattr(FinancialAccountDialog, "exec", create_account)
    page.finance.add_button.click()
    account = context.db.scalar("SELECT id FROM financial_accounts WHERE code='BANK'")
    assert page.finance.table.item(0, 3).text() == "1020"
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("حساب الموردين", True))
    page.finance.rename_button.click()
    assert context.db.scalar("SELECT name_ar FROM financial_accounts WHERE id=?", (account,)) == "حساب الموردين"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    page.finance.state_button.click()
    assert not context.db.scalar("SELECT is_active FROM financial_accounts WHERE id=?", (account,))
    page.finance.state_button.click()
    assert context.db.scalar("SELECT is_active FROM financial_accounts WHERE id=?", (account,)) == 1

    page.periods.month.setDate(QDate(2026, 10, 1))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("", True))
    page.periods.change_button.click()
    assert context.db.scalar("SELECT COUNT(*) FROM closed_periods") == 0
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("تمت المطابقة", True))
    page.periods.change_button.click()
    assert context.db.scalar("SELECT month FROM closed_periods") == "2026-10"
    assert page.periods.status.text() == "الفترة مقفلة"
    page.periods.change_button.click()
    assert context.db.scalar("SELECT COUNT(*) FROM closed_periods") == 0
    assert context.db.scalar(
        "SELECT COUNT(*) FROM audit_events WHERE event_type IN ('PERIOD_CLOSED','PERIOD_REOPENED')",
    ) == 2


def test_settings_staff_admin_and_self_service_password_are_permission_gated(qt_app, owner_context, monkeypatch):
    context, owner = owner_context
    page = SettingsPage(context, owner)
    page.users.refresh(owner)
    assert not page.users.state_button.isEnabled()
    assert not page.users.role_button.isEnabled()
    with pytest.raises(PermissionDenied):
        context.auth.set_staff_active(owner, False, actor_id=owner)

    def enter_staff(dialog):
        if dialog.reset_id is None:
            dialog.username.setText("clerk")
            dialog.name.setText("موظف")
        password = "staff-pass-1" if dialog.reset_id is None else "staff-pass-2"
        dialog.password.setText(password)
        dialog.confirm.setText(password)
        assert dialog.save()
        return dialog.result()

    monkeypatch.setattr(StaffDialog, "exec", enter_staff)
    page.users.add_button.click()
    user = context.auth.login("clerk", "staff-pass-1")["id"]
    assert context.has_permission(user, "create_sales_draft")
    assert not context.has_permission(user, "manage_users")

    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("المدير", True))
    page.users.role_button.click()
    assert context.has_permission(user, "view_profit_reports")
    with pytest.raises(PermissionDenied):
        SettingsService(context.db).save_store("آخر", "", "", actor_id=user)
    with pytest.raises(PermissionDenied):
        context.auth.reset_staff_password(owner, "unwanted-pass", actor_id=user)

    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    page.users.state_button.click()
    with pytest.raises(ValueError):
        context.auth.login("clerk", "staff-pass-1")
    page.users.state_button.click()
    page.users.password_button.click()
    with pytest.raises(ValueError):
        context.auth.login("clerk", "staff-pass-1")
    assert context.auth.login("clerk", "staff-pass-2")["id"] == user

    staff_page = SettingsPage(context, user)
    assert staff_page.tabs.count() == 1
    staff_page.account.current.setText("wrong-password")
    staff_page.account.password.setText("staff-pass-3")
    staff_page.account.confirm.setText("staff-pass-3")
    assert not staff_page.account.save()
    staff_page.account.current.setText("staff-pass-2")
    assert staff_page.account.save()
    assert context.auth.login("clerk", "staff-pass-3")["id"] == user
    assert context.db.scalar(
        "SELECT COUNT(*) FROM audit_events WHERE entity_type='user' AND entity_id=? "
        "AND event_type IN ('USER_CREATED','USER_ROLE_CHANGED','USER_DISABLED','USER_ENABLED',"
        "'USER_PASSWORD_RESET','USER_PASSWORD_CHANGED')", (user,),
    ) == 6
    assert context.db.scalar(
        "SELECT COUNT(*) FROM audit_events WHERE after_json LIKE '%staff-pass%' OR before_json LIKE '%staff-pass%'",
    ) == 0
