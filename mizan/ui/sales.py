"""Sales invoices, including draft editing and authorized posting."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace

from PySide6.QtCore import QDate, QItemSelectionModel, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..bootstrap import AppContext
from ..lcp import line_total
from ..periods import ClosedPeriodError
from ..pricing import base_quantity, resolve_price
from ..sales import BelowCostApprovalRequired, InsufficientStock, SalesService
from .common import (
    fill_table, format_scaled, make_date_field, make_hint, make_message, make_table,
    make_title, money, parse_scaled, populate_combo, product_units, query_rows, set_message,
)
from .financial_accounts import FinancialAccountDialog, financial_account_rows
from .sale_settlements import (
    ApplyCustomerCreditDialog, CustomerAccountDialog, CustomerReceiptDialog,
    SaleReturnDialog, SaleSettlementHistoryDialog,
)

STATUS = {"DRAFT": "مسودة", "POSTED": "مرحّلة", "CANCELLED": "ملغاة", "REVERSED": "معكوسة"}
PAYMENT = {"CREDIT": "آجل", "CASH": "نقدي", "MIXED": "مختلط"}
PRICE_SOURCE = {
    "CUSTOMER": "سعر العميل", "PRICE_LIST": "قائمة الأسعار", "DEFAULT": "السعر الافتراضي",
    "MANUAL": "يدوي",
}
SELECT_ROW = QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows


@dataclass(frozen=True)
class SaleLine:
    product_id: int
    product: str
    unit_id: int
    unit: str
    quantity: int
    price: int | None
    source: str | None
    discount: int = 0
    reason: str | None = None
    # Older drafts can assign a project on a line independently of the header.
    override_project: bool = False
    project_id: int | None = None
    description: str | None = None

    def values(self) -> dict:
        values = {
            "product_id": self.product_id,
            "unit_id": self.unit_id,
            "quantity_micros": self.quantity,
            "item_discount_piastres": self.discount,
        }
        if self.source == "MANUAL":
            values["unit_price_micros"] = self.price
            values["price_adjustment_reason"] = self.reason
        if self.override_project:
            values["project_id"] = self.project_id
        if self.description is not None:
            values["description"] = self.description
        return values

    @property
    def net(self) -> int | None:
        return None if self.price is None else line_total(self.price, self.quantity) - self.discount


class SaleLineDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, customer_id: int, price_list_id: int | None,
                 parent=None, *, line: SaleLine | None = None):
        super().__init__(parent)
        self.context = context
        self.customer_id = customer_id
        self.price_list_id = price_list_id
        self.original = line
        self.line: SaleLine | None = None
        self.can_edit_price = context.has_permission(user_id, "edit_sale_price")
        self.setWindowTitle("بند فاتورة بيع")
        self.setMinimumWidth(480)

        self.product = QComboBox()
        self.product.setPlaceholderText("اختر الصنف")
        populate_combo(self.product, query_rows(
            context, "SELECT id,code||' — '||name_ar FROM products WHERE is_active=1 OR id=? ORDER BY code",
            (line.product_id if line else None,),
        ))
        self.product.setCurrentIndex(self.product.findData(line.product_id) if line else -1)
        self.unit = QComboBox()
        self.quantity = QLineEdit(format_scaled(line.quantity) if line else "")
        self.manual = QCheckBox("إدخال السعر يدويًا")
        self.price = QLineEdit()
        self.reason = QLineEdit()
        self.discount = QLineEdit(format_scaled(line.discount, 2) if line else "0")
        self.discount.setEnabled(context.has_permission(user_id, "apply_item_discount"))
        self.price_label = QLabel()
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        for field in (self.quantity, self.price, self.discount):
            field.setLayoutDirection(Qt.LeftToRight)

        form = QFormLayout(self)
        form.addRow("الصنف", self.product)
        form.addRow("الوحدة", self.unit)
        form.addRow("الكمية", self.quantity)
        form.addRow("السعر المسجّل", self.price_label)
        form.addRow(self.manual)
        form.addRow("السعر اليدوي بالجنيه", self.price)
        form.addRow("سبب تعديل السعر", self.reason)
        form.addRow("خصم البند بالجنيه", self.discount)
        form.addRow(self.message)
        self.manual_price_label = form.labelForField(self.price)
        self.manual_reason_label = form.labelForField(self.reason)
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("حفظ البند", QDialogButtonBox.AcceptRole)
        self.save_button.clicked.connect(self.save)
        buttons.addButton("رجوع", QDialogButtonBox.RejectRole).setObjectName("Secondary")
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._load_units()
        if line:
            self.unit.setCurrentIndex(self.unit.findData(line.unit_id))
        self.unit.currentIndexChanged.connect(self._resolve)
        self.product.currentIndexChanged.connect(self._load_units)
        self.manual.toggled.connect(self._manual_fields)
        self._resolve()
        if line and line.source == "MANUAL" and self.manual.isEnabled():
            self.manual.setChecked(True)
            self.price.setText(format_scaled(line.price))
            self.reason.setText(line.reason or "")

    def _load_units(self) -> None:
        product = self.product.currentData()
        populate_combo(self.unit, product_units(self.context, product))
        base = self.context.db.scalar("SELECT base_unit_id FROM products WHERE id=?", (product,))
        self.unit.setCurrentIndex(self.unit.findData(base))
        self._resolve()

    def _resolve(self) -> None:
        self.price.clear()
        self.reason.clear()
        self.automatic_price = self.automatic_source = None
        ready = self.product.currentData() is not None and self.unit.currentData() is not None
        if ready:
            with closing(self.context.db.connect()) as conn:
                self.automatic_price, self.automatic_source = resolve_price(
                    conn, self.customer_id, self.product.currentData(), self.unit.currentData(), self.price_list_id,
                )
        self.manual.setEnabled(ready and (self.can_edit_price or self.automatic_price is None))
        self.manual.setChecked(ready and self.automatic_price is None)
        if not ready:
            self.price_label.setText("—")
        elif self.automatic_price is None:
            self.price_label.setText("أدخل سعرًا لهذا الصنف والوحدة.")
        else:
            self.price_label.setText(
                f"{format_scaled(self.automatic_price)} جنيه • {PRICE_SOURCE[self.automatic_source]}"
            )
        self.save_button.setEnabled(ready)
        self._manual_fields()

    def _manual_fields(self) -> None:
        manual = self.manual.isChecked()
        reason = manual and self.automatic_price is not None
        self.price.setVisible(manual)
        self.manual_price_label.setVisible(manual)
        self.reason.setVisible(reason)
        self.manual_reason_label.setVisible(reason)

    def save(self) -> bool:
        product_id, unit_id = self.product.currentData(), self.unit.currentData()
        try:
            if product_id is None or unit_id is None:
                raise ValueError("اختر الصنف والوحدة.")
            quantity = parse_scaled(self.quantity.text(), "الكمية")
            with closing(self.context.db.connect()) as conn:
                product = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
                base_quantity(conn, product, unit_id, quantity)
                price, source = resolve_price(conn, self.customer_id, product_id, unit_id, self.price_list_id)
            reason = None
            if self.manual.isChecked():
                manual_price = parse_scaled(self.price.text(), "السعر اليدوي")
                if price is None:
                    price, source = manual_price, "MANUAL"
                elif manual_price != price:
                    if not self.can_edit_price or not self.reason.text().strip():
                        raise ValueError("تعديل السعر المسجّل يتطلب صلاحية وسببًا.")
                    price, source, reason = manual_price, "MANUAL", self.reason.text().strip()
            if price is None:
                raise ValueError("أدخل سعر الوحدة.")
            discount = parse_scaled(self.discount.text().strip() or "0", "خصم البند", 2)
            if discount > line_total(price, quantity):
                raise ValueError("الخصم أكبر من قيمة البند.")
        except (ValueError, sqlite3.Error) as exc:
            set_message(self.message, str(exc), error=True)
            return False
        original = self.original
        self.line = SaleLine(
            product_id, self.product.currentText(), unit_id, self.unit.currentText(),
            quantity, price, source, discount, reason,
            original.override_project if original else False, original.project_id if original else None,
        )
        self.accept()
        return True


class SaleInvoiceDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, parent=None, *, invoice_id: int | None = None):
        super().__init__(parent)
        self.context = context
        self.user_id = user_id
        self.invoice_id = invoice_id
        self.lines: list[SaleLine] = []
        self.invoice = self._load_invoice() if invoice_id is not None else None
        invoice = self.invoice
        self.original_lines = tuple(self.lines)
        self.read_only = not context.has_permission(user_id, "create_sales_draft") or (
            invoice is not None and invoice["status"] != "DRAFT"
        )
        self.locked = self.read_only or (invoice is not None and invoice["source_quotation_id"] is not None)
        self.setWindowTitle(f"فاتورة بيع {invoice['document_no']}" if invoice else "فاتورة بيع جديدة")
        self.resize(790, 620)

        self.customer = QComboBox()
        self.customer.setPlaceholderText("اختر العميل")
        populate_combo(self.customer, query_rows(
            context, "SELECT id,code||' — '||name_ar FROM customers WHERE is_active=1 OR id=? ORDER BY code",
            (invoice["customer_id"] if invoice else None,),
        ))
        self.customer.setCurrentIndex(self.customer.findData(invoice["customer_id"]) if invoice else -1)
        self.customer.setEnabled(not self.read_only and invoice is None)
        self.price_list = QComboBox()
        self.price_list.addItem("بدون قائمة", None)
        for list_id, name in query_rows(
            context, "SELECT id,code||' — '||name_ar FROM price_lists WHERE is_active=1 OR id=? ORDER BY code",
            (invoice["price_list_id"] if invoice else None,),
        ):
            self.price_list.addItem(name, list_id)
        self.price_list.setEnabled(not self.locked)
        self.project = QComboBox()
        self.project.setEnabled(not self.locked)
        self.invoice_date = make_date_field()
        self.has_due = QCheckBox("تاريخ الاستحقاق")
        self.due_date = make_date_field()
        self.payment = QComboBox()
        for code in ("CREDIT", "CASH"):
            self.payment.addItem(PAYMENT[code], code)
        self.account = QComboBox()
        self.account.setPlaceholderText("اختر الحساب المالي")
        self._load_accounts(invoice["financial_account_id"] if invoice else None)
        self.account_label = QLabel("الحساب المالي")
        self.account_label.setBuddy(self.account)
        self.add_account = QPushButton("إضافة حساب")
        self.add_account.setObjectName("Secondary")
        self.add_account.clicked.connect(self._add_account)
        self.can_add_account = not self.read_only and context.has_permission(user_id, "manage_settings")
        self.discount = QLineEdit(format_scaled(invoice["invoice_discount_piastres"], 2) if invoice else "0")
        self.discount.setLayoutDirection(Qt.LeftToRight)
        self.discount.setEnabled(not self.locked and context.has_permission(user_id, "apply_invoice_discount"))
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        self.totals = QLabel()
        self.totals.setObjectName("Subtitle")
        self.totals.setWordWrap(True)

        layout = QVBoxLayout(self)
        fields = QGridLayout()
        for row, column, title, widget in (
            (0, 0, "العميل", self.customer), (0, 2, "تاريخ الفاتورة", self.invoice_date),
            (1, 0, "قائمة الأسعار", self.price_list), (1, 2, "المشروع", self.project),
            (2, 0, "طريقة السداد", self.payment), (3, 2, "خصم الفاتورة بالجنيه", self.discount),
        ):
            label = QLabel(title)
            label.setBuddy(widget)
            fields.addWidget(label, row, column)
            fields.addWidget(widget, row, column + 1)
        fields.addWidget(self.has_due, 2, 2)
        fields.addWidget(self.due_date, 2, 3)
        fields.addWidget(self.account_label, 3, 0)
        account_row = QHBoxLayout()
        account_row.addWidget(self.account, 1)
        account_row.addWidget(self.add_account)
        fields.addLayout(account_row, 3, 1)
        fields.setColumnStretch(1, 1)
        fields.setColumnStretch(3, 1)
        layout.addLayout(fields)

        self.table = make_table(["الصنف", "الوحدة", "الكمية", "السعر", "مصدر السعر", "خصم البند", "صافي البند"])
        layout.addWidget(self.table, 1)
        if not self.locked:
            actions = QHBoxLayout()
            for label, slot in (("إضافة بند", self._add_line), ("تعديل البند", self._edit_line),
                                ("حذف البند", self._remove_line)):
                button = QPushButton(label)
                button.setObjectName("Secondary")
                button.clicked.connect(slot)
                actions.addWidget(button)
            actions.addStretch(1)
            layout.addLayout(actions)
            self.table.cellDoubleClicked.connect(self._edit_line)
            if invoice is not None:
                layout.addWidget(make_hint("تعديل التفاصيل التجارية يعيد تطبيق الأسعار التلقائية الحالية."))
        elif invoice is not None and invoice["source_quotation_id"] is not None:
            reference = context.db.scalar("SELECT document_no FROM quotations WHERE id=?", (invoice["source_quotation_id"],))
            layout.addWidget(make_hint(f"بيانات وأسعار عرض السعر {reference or 'الأصلي'} محفوظة في الفاتورة."))
        layout.addWidget(self.totals)
        layout.addWidget(self.message)

        buttons = QDialogButtonBox()
        if not self.read_only:
            self.save_button = buttons.addButton("حفظ المسودة", QDialogButtonBox.AcceptRole)
            self.save_button.clicked.connect(self.save)
        buttons.addButton("إغلاق", QDialogButtonBox.RejectRole).setObjectName("Secondary")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_projects()
        if invoice is not None:
            self.project.setCurrentIndex(max(0, self.project.findData(invoice["project_id"])))
            self.price_list.setCurrentIndex(max(0, self.price_list.findData(invoice["price_list_id"])))
            if self.payment.findData(invoice["payment_type"]) < 0:
                self.payment.addItem(PAYMENT[invoice["payment_type"]], invoice["payment_type"])
            self.payment.setCurrentIndex(self.payment.findData(invoice["payment_type"]))
            self.invoice_date.setDate(QDate.fromString(invoice["invoice_date"][:10], Qt.ISODate))
            if invoice["due_date"]:
                self.has_due.setChecked(True)
                self.due_date.setDate(QDate.fromString(invoice["due_date"][:10], Qt.ISODate))
        self.customer.currentIndexChanged.connect(self._load_projects)
        self.customer.currentIndexChanged.connect(self._refresh_prices)
        self.price_list.currentIndexChanged.connect(self._refresh_prices)
        self.payment.currentIndexChanged.connect(self._payment_fields)
        self.has_due.toggled.connect(self._payment_fields)
        self.discount.textChanged.connect(self._preview_total)
        for field in (self.invoice_date, self.payment, self.has_due, self.account):
            field.setEnabled(not self.read_only)
        self._payment_fields()
        self._refresh_table()

    def _load_invoice(self):
        with closing(self.context.db.connect()) as conn:
            conn.execute("BEGIN")
            invoice = conn.execute("SELECT * FROM sales WHERE id=?", (self.invoice_id,)).fetchone()
            if invoice is None:
                raise ValueError("فاتورة البيع غير موجودة.")
            self.lines = [
                SaleLine(
                    row["product_id"], row["product"], row["unit_id"], row["unit"], row["quantity_micros"],
                    row["unit_price_micros"], row["price_source"], row["item_discount_piastres"],
                    row["price_adjustment_reason"], row["project_id"] != invoice["project_id"], row["project_id"],
                )
                for row in conn.execute(
                    "SELECT l.*,p.code||' — '||p.name_ar AS product,u.code||' — '||u.name_ar AS unit "
                    "FROM sales_lines l JOIN products p ON p.id=l.product_id JOIN units u ON u.id=l.unit_id "
                    "WHERE l.sales_id=? ORDER BY l.line_no", (self.invoice_id,),
                )
            ]
        return invoice

    def _load_projects(self) -> None:
        self.project.clear()
        self.project.addItem("بدون مشروع", None)
        for project_id, name in query_rows(
            self.context,
            "SELECT id,code||' — '||name_ar FROM projects WHERE customer_id=? ORDER BY code",
            (self.customer.currentData(),),
        ):
            self.project.addItem(name, project_id)

    def _load_accounts(self, selected: int | None) -> None:
        populate_combo(self.account, financial_account_rows(self.context, selected))
        self.account.setCurrentIndex(self.account.findData(selected) if selected is not None else -1)

    def _add_account(self) -> None:
        dialog = FinancialAccountDialog(self.context, self.user_id, self)
        try:
            if dialog.exec() == QDialog.Accepted:
                self._load_accounts(dialog.account_id)
        finally:
            dialog.deleteLater()

    def _payment_fields(self) -> None:
        payment = self.payment.currentData()
        self.account_label.setVisible(payment == "CASH")
        self.account.setVisible(payment == "CASH")
        self.add_account.setVisible(payment == "CASH" and self.can_add_account)
        self.has_due.setVisible(payment == "CREDIT")
        self.due_date.setVisible(payment == "CREDIT")
        self.due_date.setEnabled(not self.read_only and self.has_due.isChecked())

    def _refresh_prices(self) -> None:
        with closing(self.context.db.connect()) as conn:
            for index, line in enumerate(self.lines):
                if line.source != "MANUAL":
                    price, source = resolve_price(
                        conn, self.customer.currentData(), line.product_id, line.unit_id, self.price_list.currentData(),
                    )
                    self.lines[index] = replace(line, price=price, source=source)
        self._refresh_table()

    def _invoice_discount(self) -> int:
        return parse_scaled(self.discount.text().strip() or "0", "خصم الفاتورة", 2)

    def _preview_total(self) -> None:
        try:
            values = [line.net for line in self.lines]
            if None in values:
                raise ValueError("راجع أسعار البنود الناقصة.")
            discount = self._invoice_discount()
            net = sum(values)
            if discount > net or any(value < 0 for value in values):
                raise ValueError("راجع الخصومات المدخلة.")
            self.totals.setText(f"صافي البنود {money(net)}     خصم الفاتورة {money(discount)}     الإجمالي {money(net - discount)}")
        except ValueError as exc:
            self.totals.setText(str(exc))

    def _refresh_table(self) -> None:
        fill_table(self.table, [
            (line.product, line.unit, format_scaled(line.quantity),
             format_scaled(line.price) if line.price is not None else "—",
             PRICE_SOURCE.get(line.source, line.source or "—"),
             money(line.discount), money(line.net) if line.net is not None else "—")
            for line in self.lines
        ])
        self._preview_total()

    def _selected_line(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _get_line(self, line: SaleLine | None = None) -> SaleLine | None:
        customer = self.customer.currentData()
        if customer is None:
            set_message(self.message, "اختر العميل قبل إدخال البنود.", error=True)
            return None
        dialog = SaleLineDialog(self.context, self.user_id, customer, self.price_list.currentData(), self, line=line)
        try:
            return dialog.line if dialog.exec() == QDialog.Accepted else None
        finally:
            dialog.deleteLater()

    def _add_line(self) -> None:
        line = self._get_line()
        if line is not None:
            self.lines.append(line)
            self._refresh_prices()
            self.table.setCurrentCell(len(self.lines) - 1, 0, SELECT_ROW)

    def _edit_line(self) -> None:
        row = self._selected_line()
        if self.locked or row is None:
            return
        line = self._get_line(self.lines[row])
        if line is not None:
            self.lines[row] = line
            self._refresh_prices()
            self.table.setCurrentCell(row, 0, SELECT_ROW)

    def _remove_line(self) -> None:
        row = self._selected_line()
        if not self.locked and row is not None:
            del self.lines[row]
            self.table.clearSelection()
            self._refresh_prices()

    def save(self) -> bool:
        if self.read_only:
            return False
        try:
            customer = self.customer.currentData()
            if customer is None or not self.lines:
                raise ValueError("اختر عميلًا وأضف بندًا واحدًا على الأقل.")
            payment = self.payment.currentData()
            account = self.account.currentData() if payment == "CASH" else None
            if payment == "CASH" and account is None:
                raise ValueError("البيع النقدي يتطلب حسابًا ماليًا.")
            header = {
                "invoice_date": self.invoice_date.date().toString(Qt.ISODate),
                "payment_type": payment, "financial_account_id": account,
                "due_date": self.due_date.date().toString(Qt.ISODate)
                if payment == "CREDIT" and self.has_due.isChecked() else None,
                "actor_id": self.user_id,
            }
            service = SalesService(self.context.db)
            if self.locked:
                service.update_draft_header(self.invoice_id, **header)
            else:
                price_list = self.price_list.currentData()
                project = self.project.currentData()
                discount = self._invoice_discount()
                commercial = {
                    "lines": [line.values() for line in self.lines],
                    "price_list_id": price_list, "project_id": project,
                    "invoice_discount_piastres": discount,
                }
                if self.invoice_id is None:
                    self.invoice_id = service.create_invoice(customer, **header, **commercial)
                elif (tuple(self.lines) != self.original_lines or price_list != self.invoice["price_list_id"]
                      or project != self.invoice["project_id"] or discount != self.invoice["invoice_discount_piastres"]):
                    service.update_draft(self.invoice_id, **header, **commercial, clear_price_list=price_list is None)
                else:
                    service.update_draft_header(self.invoice_id, **header)
        except PermissionError:
            set_message(self.message, "الحساب لا يملك الصلاحية لحفظ هذه التعديلات أو الخصومات.", error=True)
            return False
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر حفظ المسودة. {exc}", error=True)
            return False
        self.accept()
        return True


class SalesPage(QWidget):
    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.service = SalesService(context.db)
        self.can_create = context.has_permission(user_id, "create_sales_draft")
        self.can_post = context.has_permission(user_id, "post_sales")
        self.can_cancel = context.has_permission(user_id, "cancel_sales_draft")
        self.can_invoice = self.can_create or self.can_post or self.can_cancel
        self.can_receipt = context.has_permission(user_id, "manage_customer_receipts")
        self.can_return = context.has_permission(user_id, "post_sales_return")
        self.can_statement = context.has_permission(user_id, "view_customer_balance")
        self.invoices: list[tuple] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.addWidget(make_title("فواتير البيع"))
        self.create_button = QPushButton("فاتورة جديدة")
        self.create_button.setVisible(self.can_create)
        self.open_button = QPushButton("فتح الفاتورة")
        self.open_button.setVisible(self.can_invoice)
        self.post_button = QPushButton("ترحيل المسودة")
        self.post_button.setVisible(self.can_post)
        self.cancel_button = QPushButton("إلغاء المسودة")
        self.cancel_button.setVisible(self.can_cancel)
        self.status = QComboBox()
        self.status.addItem("كل الحالات", "")
        for code, label in STATUS.items():
            self.status.addItem(label, code)
        actions = QHBoxLayout()
        for button in (self.create_button, self.open_button, self.post_button, self.cancel_button):
            actions.addWidget(button)
            if button is not self.create_button:
                button.setObjectName("Secondary")
        actions.addStretch(1)
        actions.addWidget(QLabel("الحالة"))
        actions.addWidget(self.status)
        layout.addLayout(actions)
        settlements = QHBoxLayout()
        self.receipt_button = QPushButton("تحصيل عميل")
        self.return_button = QPushButton("مرتجع بيع")
        self.statement_button = QPushButton("كشف حساب العميل")
        self.credit_button = QPushButton("استخدام رصيد")
        self.settlements_button = QPushButton("تسويات الفاتورة")
        for button, permission, callback in (
            (self.receipt_button, self.can_receipt, self._open_receipt),
            (self.return_button, self.can_return, self._open_return),
            (self.credit_button, self.can_receipt, self._open_credit),
            (self.settlements_button, self.can_statement, self._open_history),
            (self.statement_button, self.can_statement, self._open_statement),
        ):
            button.setObjectName("Secondary")
            button.setVisible(permission)
            button.clicked.connect(callback)
            settlements.addWidget(button)
        settlements.addStretch(1)
        layout.addLayout(settlements)
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        layout.addWidget(self.message)
        self.table = make_table(["رقم الفاتورة", "العميل", "التاريخ", "السداد", "الحالة", "الإجمالي"])
        layout.addWidget(self.table, 1)
        self.status.currentIndexChanged.connect(lambda: self.refresh())
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.cellDoubleClicked.connect(self._open_selected)
        self.create_button.clicked.connect(lambda: self._edit_invoice())
        self.open_button.clicked.connect(self._open_selected)
        self.post_button.clicked.connect(self._confirm_post)
        self.cancel_button.clicked.connect(self._cancel_selected)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self, selected_id: int | None = None) -> None:
        state = self.status.currentData()
        self.invoices = query_rows(
            self.context,
            "SELECT s.id,s.document_no,c.name_ar,s.invoice_date,s.payment_type,"
            "CASE WHEN s.reversal_journal_entry_id IS NOT NULL THEN 'REVERSED' ELSE s.status END AS state,"
            "s.total_piastres,s.customer_id FROM sales s JOIN customers c ON c.id=s.customer_id "
            "WHERE (?='' OR state=?) AND "
            "(? OR (state='POSTED' AND (? OR (? AND s.payment_type='CREDIT')))) "
            "ORDER BY s.invoice_date DESC,s.id DESC",
            (state, state, self.can_invoice, self.can_return, self.can_receipt),
        )
        self.table.clearSelection()
        fill_table(self.table, [
            (number, customer, day[:10], PAYMENT[payment], STATUS[status], money(total))
            for _, number, customer, day, payment, status, total, _ in self.invoices
        ])
        for row, invoice in enumerate(self.invoices):
            if invoice[0] == selected_id:
                self.table.setCurrentCell(row, 0, SELECT_ROW)
                break
        self._update_actions()

    def _selected(self) -> tuple | None:
        rows = self.table.selectionModel().selectedRows()
        return self.invoices[rows[0].row()] if rows else None

    def _update_actions(self) -> None:
        invoice = self._selected()
        draft = invoice is not None and invoice[5] == "DRAFT"
        self.open_button.setEnabled(invoice is not None and self.can_invoice)
        self.post_button.setEnabled(draft and self.can_post)
        self.cancel_button.setEnabled(draft and self.can_cancel)
        self.return_button.setEnabled(self.can_return and invoice is not None and invoice[5] == "POSTED")
        self.credit_button.setEnabled(self.can_receipt and invoice is not None and invoice[4:6] == ("CREDIT", "POSTED"))
        self.settlements_button.setEnabled(
            self.can_statement and invoice is not None and invoice[5] in ("POSTED", "REVERSED")
        )

    def _open_receipt(self) -> None:
        invoice = self._selected()
        self._open_settlement(
            "manage_customer_receipts", CustomerReceiptDialog,
            customer_id=invoice[7] if invoice is not None else None,
        )

    def _open_return(self) -> None:
        invoice = self._selected()
        if invoice is not None and invoice[5] == "POSTED":
            self._open_settlement("post_sales_return", SaleReturnDialog, invoice_id=invoice[0])

    def _open_statement(self) -> None:
        invoice = self._selected()
        self._open_settlement(
            "view_customer_balance", CustomerAccountDialog,
            customer_id=invoice[7] if invoice is not None else None,
        )

    def _open_credit(self) -> None:
        invoice = self._selected()
        if invoice is not None and self.credit_button.isEnabled():
            self._open_settlement("manage_customer_receipts", ApplyCustomerCreditDialog, invoice_id=invoice[0])

    def _open_history(self) -> None:
        invoice = self._selected()
        if invoice is not None and self.settlements_button.isEnabled():
            self._open_settlement("view_customer_balance", SaleSettlementHistoryDialog, invoice_id=invoice[0])

    def _open_settlement(self, permission: str, factory, **fields) -> None:
        invoice = self._selected()
        try:
            self.context.permissions.require(self.user_id, permission)
            dialog = factory(self.context, self.user_id, parent=self, **fields)
            try:
                if dialog.exec() == QDialog.Accepted:
                    feedback = getattr(dialog, "feedback", None)
                    document = getattr(dialog, "document_no", None)
                    if feedback or document:
                        self.refresh(invoice[0] if invoice is not None else None)
                        set_message(self.message, feedback or f"تم ترحيل المستند {document}.")
            finally:
                dialog.deleteLater()
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية هذه العملية.", error=True)
        except (ValueError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر فتح الشاشة. {exc}", error=True)

    def _open_selected(self) -> None:
        invoice = self._selected()
        if invoice is not None:
            self.open_invoice(invoice[0])

    def open_invoice(self, invoice_id: int) -> None:
        if self.can_invoice:
            self._edit_invoice(invoice_id)

    def _edit_invoice(self, invoice_id: int | None = None) -> None:
        try:
            if invoice_id is None:
                self.context.permissions.require(self.user_id, "create_sales_draft")
            dialog = SaleInvoiceDialog(self.context, self.user_id, self, invoice_id=invoice_id)
            try:
                if dialog.exec() == QDialog.Accepted:
                    self._refresh_result(dialog.invoice_id, "DRAFT", "حُفظت مسودة البيع.")
            finally:
                dialog.deleteLater()
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية إنشاء مسودة بيع.", error=True)
        except (ValueError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر فتح الفاتورة. {exc}", error=True)

    def _refresh_result(self, invoice_id: int, state: str, message: str) -> None:
        if self.status.currentData() not in ("", state):
            self.status.setCurrentIndex(self.status.findData(state))
        self.refresh(invoice_id)
        set_message(self.message, message)

    def _confirm_post(self) -> None:
        invoice = self._selected()
        if not self.post_button.isEnabled() or invoice is None:
            return
        if QMessageBox.question(
            self, "ترحيل فاتورة البيع", f"هل تريد ترحيل الفاتورة {invoice[1]} إلى المخزون والحسابات؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            self.post_invoice(invoice[0])

    def post_invoice(self, invoice_id: int) -> bool:
        try:
            try:
                self.service.post_invoice(invoice_id, actor_id=self.user_id)
            except BelowCostApprovalRequired:
                reason, approved = QInputDialog.getText(
                    self, "اعتماد البيع تحت التكلفة", "سبب اعتماد البيع تحت التكلفة",
                )
                if not approved or not reason.strip():
                    set_message(self.message, "المسودة محفوظة. البيع تحت التكلفة يحتاج سبب اعتماد.", error=True)
                    return False
                self.service.post_invoice(invoice_id, actor_id=self.user_id, below_cost_reason=reason)
        except PermissionError as exc:
            message = "البيع تحت التكلفة يحتاج صلاحية اعتماد." if "approve_below_cost" in str(exc) else (
                "لا تملك صلاحية ترحيل البيع."
            )
            set_message(self.message, message, error=True)
            return False
        except ClosedPeriodError:
            set_message(self.message, "الفترة المحاسبية لتاريخ الفاتورة مقفلة. المسودة محفوظة.", error=True)
            return False
        except InsufficientStock as exc:
            shortages = []
            for item in exc.shortages:
                name = self.context.db.scalar("SELECT name_ar FROM products WHERE id=?", (item["product_id"],))
                shortages.append(f"{name or item['product_id']} • المتبقي {format_scaled(item['available'])}")
            set_message(self.message, "المخزون لا يكفي بوحدات الأساس. " + "، ".join(shortages), error=True)
            return False
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر الترحيل. {exc}", error=True)
            return False
        self._refresh_result(invoice_id, "POSTED", "رُحّلت فاتورة البيع إلى المخزون والحسابات.")
        return True

    def _cancel_selected(self) -> None:
        invoice = self._selected()
        if not self.cancel_button.isEnabled() or invoice is None:
            return
        reason, accepted = QInputDialog.getText(self, "إلغاء مسودة بيع", f"سبب إلغاء الفاتورة {invoice[1]}")
        if accepted:
            try:
                self.service.cancel_draft(invoice[0], reason, actor_id=self.user_id)
            except PermissionError:
                set_message(self.message, "لا تملك صلاحية إلغاء مسودات البيع.", error=True)
                return
            except (ValueError, sqlite3.Error) as exc:
                set_message(self.message, f"تعذر إلغاء المسودة. {exc}", error=True)
                return
            self._refresh_result(invoice[0], "CANCELLED", "أُلغيت مسودة البيع.")
