"""Quotations, revisions, official A4 documents and sale conversion."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import date

from PySide6.QtCore import QDate, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from ..bootstrap import AppContext
from ..pricing import resolve_price
from ..quotation_export import QuotationExportService
from ..quotations import QuotationService
from .common import (
    fill_table, format_scaled, make_date_field, make_hint, make_message, make_table,
    make_title, money, parse_scaled, populate_combo, query_rows, set_message,
)
from .financial_accounts import financial_account_rows
from .sales import PAYMENT, PRICE_SOURCE, SELECT_ROW, SaleLine, SaleLineDialog

QUOTATION_PERMISSIONS = (
    "create_quotation", "issue_quotation", "create_quotation_revision", "accept_quotation",
    "reject_quotation", "cancel_quotation", "convert_quotation_to_sale",
)
STATUS = {
    "DRAFT": "مسودة", "ISSUED": "صادر", "ACCEPTED": "مقبول", "REJECTED": "مرفوض",
    "CANCELLED": "ملغى", "SUPERSEDED": "استُبدل بمراجعة", "CONVERTED": "تحوّل إلى بيع",
    "EXPIRED": "منتهي",
}


class QuotationLineDialog(SaleLineDialog):
    def __init__(self, context, user_id, customer_id, price_list_id, parent=None, *, line=None):
        super().__init__(context, user_id, customer_id, price_list_id, parent, line=line)
        self.setWindowTitle("بند عرض سعر")
        self.description = QLineEdit((line.description or "") if line else "")
        self.description.setPlaceholderText("يُستخدم اسم الصنف عند ترك الوصف فارغًا")
        self.layout().insertRow(2, "وصف البند في العرض", self.description)

    def save(self) -> bool:
        if not super().save():
            return False
        self.line = replace(self.line, description=self.description.text().strip() or None)
        return True


class QuotationDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, parent=None, *, quotation_id: int | None = None):
        super().__init__(parent)
        self.context = context
        self.user_id = user_id
        self.service = QuotationService(context.db)
        self.quotation_id = quotation_id
        self.quote = self.service.get_quotation(quotation_id) if quotation_id is not None else None
        quote = self.quote
        self.editable = (quote is None or quote["status"] == "DRAFT") and context.has_permission(user_id, "create_quotation")
        self.lines = [
            SaleLine(
                line["product_id"], f"{line['product_code_snapshot']} — {line['product_name_snapshot']}",
                line["unit_id"], line["unit_name_snapshot"], line["quantity_micros"],
                line["final_unit_price_micros"], line["price_source"], line["item_discount_piastres"],
                line["price_adjustment_reason"], line["project_id"] != quote["project_id"], line["project_id"],
                line["description_snapshot"],
            )
            for line in quote["lines"]
        ] if quote else []
        self.original_lines = tuple(self.lines)
        self.setWindowTitle(f"عرض سعر {quote['document_no']}" if quote else "عرض سعر جديد")
        self.resize(820, 615)

        self.customer = QComboBox()
        self.customer.setPlaceholderText("اختر العميل")
        populate_combo(self.customer, query_rows(
            context, "SELECT id,code||' — '||name_ar FROM customers WHERE is_active=1 OR id=? ORDER BY code",
            (quote["customer_id"] if quote else None,),
        ))
        self.customer.setCurrentIndex(self.customer.findData(quote["customer_id"]) if quote else -1)
        self.project = QComboBox()
        self.price_list = QComboBox()
        self.price_list.addItem("بدون قائمة", None)
        for list_id, label in query_rows(
            context, "SELECT id,code||' — '||name_ar FROM price_lists WHERE is_active=1 OR id=? ORDER BY code",
            (quote["price_list_id"] if quote else None,),
        ):
            self.price_list.addItem(label, list_id)
        self.issue_date = make_date_field()
        self.has_expiry = QCheckBox("تحديد الصلاحية")
        self.expiry = make_date_field()
        self.discount = QLineEdit(format_scaled(quote["invoice_discount_piastres"], 2) if quote else "0")
        self.discount.setLayoutDirection(Qt.LeftToRight)
        self.notes = QPlainTextEdit((quote["notes"] or "") if quote else "")
        self.terms = QPlainTextEdit((quote["terms"] or "") if quote else "")
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        self.total = QLabel()
        self.total.setObjectName("Subtitle")
        self.total.setWordWrap(True)

        layout = QVBoxLayout(self)
        header = QGridLayout()
        for row, column, text, field in (
            (0, 0, "العميل", self.customer), (0, 2, "تاريخ العرض", self.issue_date),
            (1, 0, "قائمة الأسعار", self.price_list), (1, 2, "المشروع", self.project),
            (2, 2, "خصم العرض بالجنيه", self.discount),
        ):
            label = QLabel(text)
            label.setBuddy(field)
            header.addWidget(label, row, column)
            header.addWidget(field, row, column + 1)
        header.addWidget(self.has_expiry, 2, 0)
        header.addWidget(self.expiry, 2, 1)
        header.setColumnStretch(1, 1)
        header.setColumnStretch(3, 1)
        layout.addLayout(header)

        tabs = QTabWidget()
        lines_tab = QWidget()
        lines_layout = QVBoxLayout(lines_tab)
        self.table = make_table(["الصنف / الوصف", "الوحدة", "الكمية", "السعر", "مصدر السعر", "خصم البند", "صافي البند"])
        lines_layout.addWidget(self.table, 1)
        if self.editable:
            actions = QHBoxLayout()
            self.add_line_button = QPushButton("إضافة بند")
            for button, slot in (
                (self.add_line_button, self._add_line), (QPushButton("تعديل البند"), self._edit_line),
                (QPushButton("حذف البند"), self._remove_line),
            ):
                button.setObjectName("Secondary")
                button.clicked.connect(slot)
                actions.addWidget(button)
            actions.addStretch(1)
            lines_layout.addLayout(actions)
            self.table.cellDoubleClicked.connect(self._edit_line)
            if quote:
                lines_layout.addWidget(make_hint("تعديل البنود أو التسعير يعيد تطبيق الأسعار التلقائية الحالية."))
        tabs.addTab(lines_tab, "البنود")
        text_tab = QWidget()
        text_fields = QFormLayout(text_tab)
        text_fields.addRow("الملاحظات", self.notes)
        text_fields.addRow("الشروط", self.terms)
        tabs.addTab(text_tab, "الشروط والملاحظات")
        layout.addWidget(tabs, 1)
        layout.addWidget(self.total)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox()
        if self.editable:
            self.save_button = buttons.addButton("حفظ المسودة", QDialogButtonBox.AcceptRole)
            self.save_button.clicked.connect(self.save)
        buttons.addButton("إغلاق", QDialogButtonBox.RejectRole).setObjectName("Secondary")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_projects()
        if quote:
            self.project.setCurrentIndex(max(0, self.project.findData(quote["project_id"])))
            self.price_list.setCurrentIndex(max(0, self.price_list.findData(quote["price_list_id"])))
            self.issue_date.setDate(QDate.fromString(quote["issue_date"], Qt.ISODate))
            if quote["valid_until"]:
                self.has_expiry.setChecked(True)
                self.expiry.setDate(QDate.fromString(quote["valid_until"], Qt.ISODate))
        self.customer.currentIndexChanged.connect(self._customer_changed)
        self.price_list.currentIndexChanged.connect(self._reprice)
        self.discount.textChanged.connect(self._preview_total)
        self.has_expiry.toggled.connect(self._expiry_state)
        for field in (self.customer, self.project, self.price_list, self.issue_date, self.has_expiry):
            field.setEnabled(self.editable)
        self.discount.setEnabled(self.editable and context.has_permission(user_id, "apply_invoice_discount"))
        self.notes.setReadOnly(not self.editable)
        self.terms.setReadOnly(not self.editable)
        self._expiry_state()
        self._refresh_table()

    def _expiry_state(self) -> None:
        self.expiry.setEnabled(self.editable and self.has_expiry.isChecked())

    def _load_projects(self) -> None:
        self.project.clear()
        self.project.addItem("بدون مشروع", None)
        for project_id, label in query_rows(
            self.context, "SELECT id,code||' — '||name_ar FROM projects WHERE customer_id=? ORDER BY code",
            (self.customer.currentData(),),
        ):
            self.project.addItem(label, project_id)

    def _customer_changed(self) -> None:
        self._load_projects()
        self.lines = [replace(line, override_project=False, project_id=None) for line in self.lines]
        self._reprice()

    def _reprice(self) -> None:
        if self.lines:
            with closing(self.context.db.connect()) as conn:
                for index, line in enumerate(self.lines):
                    if line.source != "MANUAL":
                        price, source = resolve_price(
                            conn, self.customer.currentData(), line.product_id, line.unit_id, self.price_list.currentData(),
                        )
                        self.lines[index] = replace(line, price=price, source=source)
        self._refresh_table()

    def _discount(self) -> int:
        return parse_scaled(self.discount.text().strip() or "0", "خصم العرض", 2)

    def _preview_total(self) -> None:
        try:
            nets = [line.net for line in self.lines]
            if any(value is None or value < 0 for value in nets):
                raise ValueError("راجع أسعار البنود وخصوماتها.")
            discount = self._discount()
            subtotal = sum(nets)
            if discount > subtotal:
                raise ValueError("خصم العرض أكبر من صافي البنود.")
            self.total.setText(f"صافي البنود {money(subtotal)}     خصم العرض {money(discount)}     الإجمالي {money(subtotal - discount)}")
        except ValueError as exc:
            self.total.setText(str(exc))

    def _refresh_table(self) -> None:
        fill_table(self.table, [
            (line.description or line.product, line.unit, format_scaled(line.quantity),
             format_scaled(line.price) if line.price is not None else "—",
             PRICE_SOURCE.get(line.source, "—"), money(line.discount),
             money(line.net) if line.net is not None else "—")
            for line in self.lines
        ])
        self._preview_total()

    def _selection(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _line_dialog(self, line: SaleLine | None = None) -> SaleLine | None:
        customer = self.customer.currentData()
        if customer is None:
            set_message(self.message, "اختر العميل قبل إدخال البنود.", error=True)
            return None
        dialog = QuotationLineDialog(
            self.context, self.user_id, customer, self.price_list.currentData(), self, line=line,
        )
        try:
            return dialog.line if dialog.exec() == QDialog.Accepted else None
        finally:
            dialog.deleteLater()

    def _add_line(self) -> None:
        line = self._line_dialog()
        if line is not None:
            self.lines.append(line)
            self._reprice()
            self.table.setCurrentCell(len(self.lines) - 1, 0, SELECT_ROW)

    def _edit_line(self) -> None:
        row = self._selection()
        if row is not None and self.editable:
            line = self._line_dialog(self.lines[row])
            if line is not None:
                self.lines[row] = line
                self._reprice()
                self.table.setCurrentCell(row, 0, SELECT_ROW)

    def _remove_line(self) -> None:
        row = self._selection()
        if row is not None and self.editable:
            del self.lines[row]
            self.table.clearSelection()
            self._reprice()

    def save(self) -> bool:
        if not self.editable:
            return False
        try:
            customer = self.customer.currentData()
            if customer is None or not self.lines:
                raise ValueError("اختر عميلًا وأضف بندًا واحدًا على الأقل.")
            discount, project, price_list = self._discount(), self.project.currentData(), self.price_list.currentData()
            fields = {
                "customer_id": customer, "project_id": project, "price_list_id": price_list,
                "invoice_discount_piastres": discount,
                "issue_date": self.issue_date.date().toString(Qt.ISODate),
                "valid_until": self.expiry.date().toString(Qt.ISODate) if self.has_expiry.isChecked() else None,
                "notes": self.notes.toPlainText().strip() or None,
                "terms": self.terms.toPlainText().strip() or None, "actor_id": self.user_id,
            }
            payload = [line.values() for line in self.lines]
            if self.quotation_id is None:
                self.quotation_id = self.service.create_quotation(lines=payload, **fields)
            else:
                quote = self.quote
                commercial_changed = (
                    tuple(self.lines) != self.original_lines or discount != quote["invoice_discount_piastres"]
                    or customer != quote["customer_id"] or project != quote["project_id"]
                    or price_list != quote["price_list_id"]
                )
                self.service.update_draft(self.quotation_id, payload if commercial_changed else None, **fields)
        except PermissionError:
            set_message(self.message, "الحساب لا يملك صلاحية حفظ هذه التعديلات أو الخصومات.", error=True)
            return False
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر حفظ العرض. {exc}", error=True)
            return False
        self.accept()
        return True


class QuoteConversionDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, quotation_id: int, parent=None):
        super().__init__(parent)
        self.user_id = user_id
        self.quotation_id = quotation_id
        self.service = QuotationService(context.db)
        self.expired = self.service.is_expired(quotation_id)
        self.result_data: dict | None = None
        self.setWindowTitle("تحويل العرض إلى مسودة بيع")
        self.setMinimumWidth(470)
        self.invoice_date = make_date_field()
        self.payment = QComboBox()
        for code in ("CREDIT", "CASH"):
            self.payment.addItem(PAYMENT[code], code)
        self.has_due = QCheckBox("تاريخ الاستحقاق")
        self.due = make_date_field()
        self.account = QComboBox()
        self.account.setPlaceholderText("اختر الحساب المالي")
        populate_combo(self.account, financial_account_rows(context))
        self.account.setCurrentIndex(-1)
        self.reason = QLineEdit()
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        self.form = QFormLayout(self)
        self.form.addRow("تاريخ الفاتورة", self.invoice_date)
        self.form.addRow("السداد", self.payment)
        self.form.addRow(self.has_due, self.due)
        self.form.addRow("الحساب المالي", self.account)
        if self.expired:
            self.form.addRow("سبب تجاوز انتهاء العرض", self.reason)
        self.form.addRow(self.message)
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("إنشاء مسودة البيع", QDialogButtonBox.AcceptRole)
        self.save_button.clicked.connect(self.save)
        buttons.addButton("إغلاق", QDialogButtonBox.RejectRole).setObjectName("Secondary")
        buttons.rejected.connect(self.reject)
        self.form.addRow(buttons)
        self.payment.currentIndexChanged.connect(self._payment_fields)
        self.has_due.toggled.connect(self._payment_fields)
        self._payment_fields()

    def _payment_fields(self) -> None:
        cash = self.payment.currentData() == "CASH"
        self.account.setVisible(cash)
        self.form.labelForField(self.account).setVisible(cash)
        self.has_due.setVisible(not cash)
        self.due.setVisible(not cash)
        self.due.setEnabled(self.has_due.isChecked())

    def save(self) -> bool:
        try:
            payment = self.payment.currentData()
            account = self.account.currentData() if payment == "CASH" else None
            if payment == "CASH" and account is None:
                raise ValueError("اختر حسابًا ماليًا للبيع النقدي.")
            reason = self.reason.text().strip()
            if self.expired and not reason:
                raise ValueError("سجّل سبب تجاوز انتهاء العرض.")
            self.result_data = self.service.convert_to_sale(
                self.quotation_id, payment_type=payment, financial_account_id=account,
                invoice_date=self.invoice_date.date().toString(Qt.ISODate),
                due_date=self.due.date().toString(Qt.ISODate)
                if payment == "CREDIT" and self.has_due.isChecked() else None,
                override_expired=self.expired, override_reason=reason, actor_id=self.user_id,
            )
        except PermissionError:
            set_message(self.message, "الحساب لا يملك صلاحية التحويل أو تجاوز انتهاء العرض.", error=True)
            return False
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر إنشاء مسودة البيع. {exc}", error=True)
            return False
        self.accept()
        return True


@dataclass(frozen=True)
class QuotationRow:
    id: int
    number: str
    customer: str
    issue_date: str
    valid_until: str | None
    status: str
    total: int
    export_id: int | None
    is_latest: bool
    sale_id: int | None
    sale_number: str | None
    effective_status: str


class QuotationsPage(QWidget):
    open_sale_requested = Signal(int)

    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.service = QuotationService(context.db)
        self.exports = QuotationExportService(context.db, documents_root=context.paths.documents_root)
        self.permissions = context.permissions.permissions(user_id)
        self.rows: list[QuotationRow] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.addWidget(make_title("عروض الأسعار"))

        self.status = QComboBox()
        self.status.addItem("كل الحالات", "")
        for key, label in STATUS.items():
            self.status.addItem(label, key)
        self.create_button = QPushButton("عرض جديد")
        self.create_button.setVisible("create_quotation" in self.permissions)
        self.open_button = QPushButton("فتح العرض")
        self.issue_button = QPushButton("إصدار PDF")
        self.issue_button.setVisible("issue_quotation" in self.permissions)
        self.pdf_button = QPushButton("فتح PDF")
        actions = QHBoxLayout()
        for button in (self.create_button, self.open_button, self.issue_button, self.pdf_button):
            if button is not self.create_button:
                button.setObjectName("Secondary")
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(QLabel("الحالة"))
        actions.addWidget(self.status)
        layout.addLayout(actions)

        more = QHBoxLayout()
        self.revise_button = QPushButton("مراجعة جديدة")
        self.convert_button = QPushButton("تحويل إلى بيع")
        self.sale_button = QPushButton("فتح فاتورة البيع")
        self.lifecycle_button = QPushButton("إجراءات العرض")
        for button, visible in (
            (self.revise_button, "create_quotation_revision" in self.permissions),
            (self.convert_button, "convert_quotation_to_sale" in self.permissions),
            (self.sale_button, bool(self.permissions & {"create_sales_draft", "post_sales", "cancel_sales_draft"})),
            (self.lifecycle_button, True),
        ):
            button.setObjectName("Secondary")
            button.setVisible(visible)
            more.addWidget(button)
        more.addStretch(1)
        layout.addLayout(more)
        menu = QMenu(self.lifecycle_button)
        self.accept_action = menu.addAction("قبول العرض", self._accept)
        self.reject_action = menu.addAction("رفض العرض", self._reject)
        self.cancel_action = menu.addAction("إلغاء العرض", self._cancel)
        for action, permission in (
            (self.accept_action, "accept_quotation"), (self.reject_action, "reject_quotation"),
            (self.cancel_action, "cancel_quotation"),
        ):
            action.setVisible(permission in self.permissions)
        menu.addSeparator()
        self.print_action = menu.addAction("طباعة A4", self._print_selected)
        self.history_action = menu.addAction("سجل حالة العرض", self._history)
        self.lifecycle_button.setMenu(menu)

        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        layout.addWidget(self.message)
        self.table = make_table(["رقم العرض", "العميل", "التاريخ", "الصلاحية", "الحالة", "الإجمالي", "فاتورة البيع"])
        layout.addWidget(self.table, 1)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.cellDoubleClicked.connect(self._open_selected)
        self.status.currentIndexChanged.connect(lambda: self.refresh())
        self.create_button.clicked.connect(lambda: self._edit())
        self.open_button.clicked.connect(self._open_selected)
        self.issue_button.clicked.connect(self._confirm_issue)
        self.pdf_button.clicked.connect(self._open_pdf)
        self.revise_button.clicked.connect(self._revise)
        self.convert_button.clicked.connect(self._convert)
        self.sale_button.clicked.connect(self._open_sale)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        selected = self._selected()
        self.refresh(selected.id if selected else None)

    def refresh(self, selected_id: int | None = None) -> None:
        status = self.status.currentData()
        self.rows = [QuotationRow(*row) for row in query_rows(
            self.context,
            "SELECT q.id,q.document_no,c.name_ar,q.issue_date,q.valid_until,q.status,q.total_piastres,"
            "q.official_export_id,q.version_no=(SELECT MAX(r.version_no) FROM quotations r WHERE r.series_id=q.series_id),"
            "s.id,s.document_no,CASE WHEN q.valid_until<? AND q.status IN ('DRAFT','ISSUED','ACCEPTED') "
            "THEN 'EXPIRED' ELSE q.status END AS effective_status "
            "FROM quotations q JOIN customers c ON c.id=q.customer_id "
            "LEFT JOIN sales s ON s.source_quotation_id=q.id AND s.status<>'CANCELLED' "
            "WHERE (?='' OR effective_status=?) ORDER BY q.issue_date DESC,q.id DESC",
            (date.today().isoformat(), status, status),
        )]
        self.table.clearSelection()
        fill_table(self.table, [
            (row.number, row.customer, row.issue_date, row.valid_until or "—",
             STATUS[row.effective_status], money(row.total), row.sale_number or "—")
            for row in self.rows
        ])
        for index, row in enumerate(self.rows):
            if row.id == selected_id:
                self.table.setCurrentCell(index, 0, SELECT_ROW)
                break
        self._update_actions()

    def _selected(self) -> QuotationRow | None:
        selected = self.table.selectionModel().selectedRows()
        return self.rows[selected[0].row()] if selected else None

    def _update_actions(self) -> None:
        row = self._selected()
        status = row.status if row else None
        issued = status in ("ISSUED", "ACCEPTED")
        has_pdf = row is not None and row.export_id is not None
        self.open_button.setEnabled(row is not None)
        self.issue_button.setEnabled(status == "DRAFT")
        self.pdf_button.setEnabled(has_pdf)
        self.print_action.setEnabled(has_pdf)
        self.history_action.setEnabled(row is not None)
        self.lifecycle_button.setEnabled(row is not None)
        self.accept_action.setEnabled(status == "ISSUED")
        self.reject_action.setEnabled(status == "ISSUED")
        self.cancel_action.setEnabled(status in ("DRAFT", "ISSUED"))
        self.revise_button.setEnabled(bool(issued and row.is_latest and row.sale_id is None))
        self.convert_button.setEnabled(bool(
            issued and row.sale_id is None and (
                row.effective_status != "EXPIRED" or "override_expired_quotation" in self.permissions
            )
        ))
        self.sale_button.setEnabled(row is not None and row.sale_id is not None)

    def _success(self, quotation_id: int, message: str) -> None:
        self.refresh(quotation_id)
        if self._selected() is None and self.status.currentIndex() != 0:
            self.status.setCurrentIndex(0)
            self.refresh(quotation_id)
        set_message(self.message, message)

    def _error(self, error: Exception) -> None:
        text = "الحساب لا يملك صلاحية هذه العملية." if isinstance(error, PermissionError) else str(error)
        set_message(self.message, text, error=True)

    def _edit(self, quotation_id: int | None = None) -> None:
        try:
            if quotation_id is None:
                self.context.permissions.require(self.user_id, "create_quotation")
            dialog = QuotationDialog(self.context, self.user_id, self, quotation_id=quotation_id)
            try:
                if dialog.exec() == QDialog.Accepted:
                    self._success(dialog.quotation_id, "حُفظت مسودة العرض.")
            finally:
                dialog.deleteLater()
        except (ValueError, sqlite3.Error, PermissionError) as exc:
            self._error(exc)

    def _open_selected(self) -> None:
        row = self._selected()
        if row:
            self._edit(row.id)

    def _confirm_issue(self) -> None:
        row = self._selected()
        if row and row.status == "DRAFT" and QMessageBox.question(
            self, "إصدار عرض السعر", f"هل تريد اعتماد الإصدار وحفظ PDF رسمي للعرض {row.number}؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            self.issue(row.id)

    def issue(self, quotation_id: int) -> bool:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.exports.issue_official(quotation_id, actor_id=self.user_id)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._error(exc)
            return False
        finally:
            QApplication.restoreOverrideCursor()
        self._success(quotation_id, f"صدر العرض {result['document_no']} وحُفظ PDF الرسمي.")
        return True

    def _open_pdf(self) -> None:
        row = self._selected()
        if row and row.export_id is not None:
            try:
                pdf = self.exports.reprint_pdf(row.id)
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf["path"]))):
                    raise ValueError("تعذر فتح قارئ PDF على هذا الجهاز.")
            except (ValueError, OSError, sqlite3.Error) as exc:
                self._error(exc)

    def _print_selected(self) -> None:
        row = self._selected()
        if row and row.export_id is not None:
            printer = QPrinter(QPrinter.HighResolution)
            dialog = QPrintDialog(printer, self)
            try:
                if dialog.exec() == QDialog.Accepted:
                    self.exports.print_official(row.id, printer)
                    set_message(self.message, "أُرسل العرض الرسمي إلى الطابعة.")
            except (ValueError, OSError, sqlite3.Error) as exc:
                self._error(exc)
            finally:
                dialog.deleteLater()

    def _transition(self, action, title: str, field: str, *, required: bool) -> None:
        row = self._selected()
        if row is None:
            return
        reason, accepted = QInputDialog.getText(
            self, title, "السبب" if required else "ملاحظة (اختيارية)",
        )
        if accepted:
            try:
                action(row.id, **{field: reason.strip()}, actor_id=self.user_id)
                self._success(row.id, "تم تحديث حالة العرض.")
            except (ValueError, sqlite3.Error, PermissionError) as exc:
                self._error(exc)

    def _accept(self) -> None:
        self._transition(self.service.accept_quotation, "قبول العرض", "note", required=False)

    def _reject(self) -> None:
        self._transition(self.service.reject_quotation, "رفض العرض", "reason", required=True)

    def _cancel(self) -> None:
        self._transition(self.service.cancel_quotation, "إلغاء العرض", "reason", required=True)

    def _revise(self) -> None:
        row = self._selected()
        if row and self.revise_button.isEnabled():
            try:
                revision = self.service.create_revision(row.id, actor_id=self.user_id)
                self._success(revision, "أُنشئت مسودة مراجعة جديدة.")
                self._edit(revision)
            except (ValueError, sqlite3.Error, PermissionError) as exc:
                self._error(exc)

    def _convert(self) -> None:
        row = self._selected()
        if row and self.convert_button.isEnabled():
            try:
                self.context.permissions.require(self.user_id, "convert_quotation_to_sale")
                dialog = QuoteConversionDialog(self.context, self.user_id, row.id, self)
                try:
                    if dialog.exec() == QDialog.Accepted:
                        conversion = dialog.result_data
                        message = f"أُنشئت مسودة البيع {conversion['document_no']}."
                        if conversion["shortages"]:
                            message += " نقص استرشادي بوحدات الأساس: "
                            details = []
                            for entry in conversion["shortages"]:
                                name = self.context.db.scalar("SELECT name_ar FROM products WHERE id=?", (entry["product_id"],))
                                details.append(f"{name or entry['product_id']} {format_scaled(entry['shortage'])}")
                            message += "، ".join(details)
                        self._success(row.id, message)
                finally:
                    dialog.deleteLater()
            except (ValueError, sqlite3.Error, PermissionError) as exc:
                self._error(exc)

    def _open_sale(self) -> None:
        row = self._selected()
        if row and row.sale_id is not None:
            self.open_sale_requested.emit(row.sale_id)

    def _history(self) -> None:
        row = self._selected()
        if row is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"سجل حالة العرض — {row.number}")
        dialog.resize(750, 430)
        layout = QVBoxLayout(dialog)
        table = make_table(["التاريخ", "من", "إلى", "الإجراء", "السبب"])
        label = {
            "REVISION_CREATED": "مراجعة جديدة", "SUPERSEDED_BY_REVISION": "إصدار المراجعة",
            "CONVERSION_DRAFT_CREATED": "إنشاء مسودة بيع", "CONVERSION_DRAFT_CANCELLED": "إلغاء مسودة البيع",
            "SALES_POSTED": "ترحيل البيع",
        }
        try:
            events = self.service.status_history(row.id)
            fill_table(table, [
                (event["occurred_at"].replace("T", " ")[:19],
                 STATUS.get(event["from_status"], "—"), STATUS.get(event["to_status"], "—"),
                 label.get(event["metadata"].get("event"), STATUS.get(event["to_status"], "—")),
                 event["reason"] or event["metadata"].get("override_reason") or "—")
                for event in events
            ])
            layout.addWidget(table, 1)
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            buttons.button(QDialogButtonBox.Close).setText("إغلاق")
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            dialog.exec()
        except (ValueError, sqlite3.Error) as exc:
            self._error(exc)
        finally:
            dialog.deleteLater()
