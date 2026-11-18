"""Purchase invoice list, draft editor and posting."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass

from PySide6.QtCore import QDate, QItemSelectionModel, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from ..bootstrap import AppContext
from ..lcp import line_total
from ..periods import ClosedPeriodError
from ..pricing import base_quantity
from ..purchasing import PurchasingService
from .common import (
    fill_table, format_scaled, make_date_field, make_message, make_table, make_title, money,
    parse_scaled, populate_combo, product_units, query_rows, set_message,
)
from .financial_accounts import FinancialAccountDialog, financial_account_rows
from .purchase_settlements import (
    PurchaseReturnDialog, PurchaseSettlementHistoryDialog, SupplierPaymentDialog,
)

STATUS_LABELS = {"DRAFT": "مسودة", "POSTED": "مرحّلة", "CANCELLED": "ملغاة"}
PAYMENT_LABELS = {"CREDIT": "آجل", "CASH": "نقدي"}
SELECT_ROW = QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows


@dataclass(frozen=True)
class PurchaseLine:
    product_id: int
    product_name: str
    unit_id: int
    unit_name: str
    quantity_micros: int
    unit_price_micros: int
    reel_lengths_micros: tuple[int, ...] = ()

    def values(self) -> dict:
        return {
            "product_id": self.product_id,
            "unit_id": self.unit_id,
            "quantity_micros": self.quantity_micros,
            "unit_price_micros": self.unit_price_micros,
            "reel_lengths_micros": self.reel_lengths_micros,
        }

    def total(self) -> int:
        return line_total(self.unit_price_micros, self.quantity_micros)


class PurchaseLineDialog(QDialog):
    def __init__(self, context: AppContext, parent=None, *, line: PurchaseLine | None = None):
        super().__init__(parent)
        self.context = context
        self.line: PurchaseLine | None = None
        self.setWindowTitle("بند فاتورة شراء")
        self.setMinimumWidth(470)

        self.product = QComboBox()
        self.product.setPlaceholderText("اختر الصنف")
        self.unit = QComboBox()
        self.quantity = QLineEdit()
        self.price = QLineEdit()
        self.quantity.setLayoutDirection(Qt.LeftToRight)
        self.price.setLayoutDirection(Qt.LeftToRight)
        self.lengths = QLineEdit()
        self.lengths.setLayoutDirection(Qt.LeftToRight)
        self.lengths.setPlaceholderText("50، 50")
        self.lengths_label = QLabel()
        self.lengths_label.setBuddy(self.lengths)
        self.tracking = QLabel()
        self.message = make_message()
        self.save_button = QPushButton("حفظ البند")
        self.save_button.clicked.connect(self.save)

        form = QFormLayout(self)
        form.addRow("الصنف", self.product)
        form.addRow("الوحدة", self.unit)
        form.addRow("الكمية", self.quantity)
        form.addRow("سعر الوحدة بالجنيه", self.price)
        form.addRow("التتبّع", self.tracking)
        form.addRow(self.lengths_label, self.lengths)
        form.addRow(self.message)
        buttons = QDialogButtonBox()
        buttons.addButton(self.save_button, QDialogButtonBox.AcceptRole)
        buttons.addButton("رجوع", QDialogButtonBox.RejectRole).setObjectName("Secondary")
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        data = query_rows(
            context,
            "SELECT p.id,p.code||' — '||p.name_ar,p.tracking_mode,u.id,u.name_ar "
            "FROM products p JOIN units u ON u.id=p.base_unit_id "
            "WHERE p.is_active=1 OR p.id=? ORDER BY p.code",
            (line.product_id if line else None,),
        )
        self.products = {product_id: (mode, unit_id, name) for product_id, _, mode, unit_id, name in data}
        populate_combo(self.product, [(product_id, name) for product_id, name, *_ in data])
        if line:
            self.product.setCurrentIndex(self.product.findData(line.product_id))
            self.quantity.setText(format_scaled(line.quantity_micros))
            self.price.setText(format_scaled(line.unit_price_micros))
        else:
            self.product.setCurrentIndex(-1)
        self._select_product()
        if line:
            self.unit.setCurrentIndex(self.unit.findData(line.unit_id))
            self.lengths.setText("، ".join(map(format_scaled, line.reel_lengths_micros)))
        self.product.currentIndexChanged.connect(self._select_product)

    def _select_product(self) -> None:
        product_id = self.product.currentData()
        populate_combo(self.unit, product_units(self.context, product_id))
        mode, base_unit, unit_name = self.products.get(product_id, ("", None, ""))
        if base_unit is not None:
            self.unit.setCurrentIndex(self.unit.findData(base_unit))
        is_reel = mode == "REEL"
        self.tracking.setText({"LOT": "دفعات", "REEL": "بكرات"}.get(mode, "—"))
        self.lengths_label.setText(f"أطوال البكرات ({unit_name})")
        self.lengths_label.setVisible(is_reel)
        self.lengths.setVisible(is_reel)
        self.lengths.clear()
        self.save_button.setEnabled(product_id is not None)
        if not self.products:
            set_message(self.message, "أضف صنفًا من البيانات الأساسية.", error=True)

    def save(self) -> bool:
        product_id, unit_id = self.product.currentData(), self.unit.currentData()
        if product_id is None or unit_id is None:
            return False
        try:
            quantity = parse_scaled(self.quantity.text(), "الكمية")
            if quantity == 0:
                raise ValueError("الكمية لازم تكون أكبر من صفر.")
            price = parse_scaled(self.price.text(), "سعر الوحدة")
            mode, base_unit_id, _ = self.products[product_id]
            with closing(self.context.db.connect()) as conn:
                base, _, _ = base_quantity(
                    conn, {"id": product_id, "base_unit_id": base_unit_id}, unit_id, quantity,
                )
            lengths = ()
            if mode == "REEL":
                parts = re.split(r"[,،;؛\s]+", self.lengths.text().strip())
                lengths = tuple(parse_scaled(part, "طول البكرة") for part in parts)
                if not all(lengths) or sum(lengths) != base:
                    raise ValueError("أدخل أطوالًا موجبة مجموعها يساوي الكمية بوحدة الأساس.")
        except (ValueError, sqlite3.Error) as exc:
            set_message(self.message, str(exc), error=True)
            return False
        self.line = PurchaseLine(
            product_id, self.product.currentText(), unit_id, self.unit.currentText(), quantity, price, lengths,
        )
        self.accept()
        return True


class PurchaseInvoiceDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, parent=None, *, invoice_id: int | None = None):
        super().__init__(parent)
        self.context = context
        self.user_id = user_id
        self.invoice_id = invoice_id
        self.lines: list[PurchaseLine] = []
        invoice = self._load_invoice() if invoice_id is not None else None
        self.read_only = invoice is not None and invoice["status"] != "DRAFT"
        self.setWindowTitle(f"فاتورة شراء {invoice['document_no']}" if invoice else "فاتورة شراء جديدة")
        self.resize(760, 575)

        self.supplier = QComboBox()
        self.supplier.setPlaceholderText("اختر المورد")
        populate_combo(self.supplier, query_rows(
            context,
            "SELECT id,code||' — '||name_ar FROM suppliers WHERE is_active=1 OR id=? ORDER BY code",
            (invoice["supplier_id"] if invoice else None,),
        ))
        self.invoice_date = make_date_field()
        self.supplier_reference = QLineEdit()
        self.payment = QComboBox()
        for code, label in PAYMENT_LABELS.items():
            self.payment.addItem(label, code)
        self.account = QComboBox()
        self.account.setPlaceholderText("اختر الحساب")
        self.account_label = QLabel("الحساب المالي")
        self.account_label.setBuddy(self.account)
        self.add_account = QPushButton("إضافة حساب")
        self.add_account.setObjectName("Secondary")
        self.add_account.clicked.connect(self._add_account)
        self.can_add_account = not self.read_only and context.has_permission(user_id, "manage_settings")
        self._load_accounts(invoice["financial_account_id"] if invoice else None)

        self.has_due = QCheckBox("تاريخ الاستحقاق")
        self.due_date = make_date_field()
        self.expense = QLineEdit("0")
        self.expense.setLayoutDirection(Qt.LeftToRight)
        self.message = make_message()
        self.totals = QLabel()
        self.totals.setObjectName("Subtitle")

        layout = QVBoxLayout(self)
        fields = QGridLayout()
        for row, column, text, control in (
            (0, 0, "المورد", self.supplier),
            (0, 2, "تاريخ الفاتورة", self.invoice_date),
            (1, 0, "رقم فاتورة المورد", self.supplier_reference),
            (1, 2, "طريقة السداد", self.payment),
            (2, 2, "مصروف مباشر (جنيه)", self.expense),
        ):
            label = QLabel(text)
            label.setBuddy(control)
            fields.addWidget(label, row, column)
            fields.addWidget(control, row, column + 1)
        fields.addWidget(self.has_due, 2, 0)
        fields.addWidget(self.due_date, 2, 1)
        fields.addWidget(self.account_label, 3, 0)
        fields.addWidget(self.account, 3, 1)
        fields.addWidget(self.add_account, 3, 3)
        fields.setColumnStretch(1, 1)
        fields.setColumnStretch(3, 1)
        layout.addLayout(fields)

        self.line_table = make_table(["الصنف", "الوحدة", "الكمية", "سعر الوحدة", "البكرات", "الإجمالي"])
        layout.addWidget(self.line_table, 1)
        tools = QHBoxLayout()
        for caption, callback in (
            ("إضافة بند", self._add_line),
            ("تعديل البند", self._edit_line),
            ("حذف البند", self._remove_line),
        ):
            action = QPushButton(caption)
            action.setObjectName("Secondary")
            action.clicked.connect(callback)
            action.setVisible(not self.read_only)
            tools.addWidget(action)
        tools.addStretch(1)
        layout.addLayout(tools)
        layout.addWidget(self.totals)
        layout.addWidget(self.message)

        buttons = QDialogButtonBox()
        if not self.read_only:
            self.save_button = buttons.addButton("حفظ المسودة", QDialogButtonBox.AcceptRole)
            self.save_button.clicked.connect(self.save)
            self.save_button.setAutoDefault(False)
            self.line_table.cellDoubleClicked.connect(self._edit_line)
        buttons.addButton("إغلاق", QDialogButtonBox.RejectRole).setObjectName("Secondary")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if invoice:
            self.supplier.setCurrentIndex(self.supplier.findData(invoice["supplier_id"]))
            self.invoice_date.setDate(QDate.fromString(invoice["invoice_date"][:10], Qt.ISODate))
            self.supplier_reference.setText(invoice["supplier_invoice_no"] or "")
            self.payment.setCurrentIndex(self.payment.findData(invoice["payment_type"]))
            self.expense.setText(format_scaled(invoice["expense_piastres"], 2))
            if invoice["due_date"]:
                self.has_due.setChecked(True)
                self.due_date.setDate(QDate.fromString(invoice["due_date"], Qt.ISODate))
        else:
            self.supplier.setCurrentIndex(-1)
        self.payment.currentIndexChanged.connect(self._update_payment_fields)
        self.has_due.toggled.connect(self._update_payment_fields)
        self.expense.textChanged.connect(self._update_totals)
        if self.read_only:
            for control in (
                self.supplier, self.invoice_date, self.supplier_reference, self.payment,
                self.has_due, self.expense, self.account,
            ):
                control.setEnabled(False)
        self._update_payment_fields()
        self._refresh_lines()
        if self.supplier.count() == 0:
            set_message(self.message, "أضف موردًا من البيانات الأساسية قبل إنشاء الفاتورة.", error=True)

    def _load_invoice(self):
        with closing(self.context.db.connect()) as conn:
            conn.execute("BEGIN")
            invoice = conn.execute("SELECT * FROM purchase_invoices WHERE id=?", (self.invoice_id,)).fetchone()
            if invoice is None:
                raise ValueError("فاتورة الشراء غير موجودة.")
            reels = defaultdict(list)
            for line_id, length in conn.execute(
                "SELECT r.purchase_invoice_line_id,r.length_micros FROM purchase_reel_inputs r "
                "JOIN purchase_invoice_lines l ON l.id=r.purchase_invoice_line_id "
                "WHERE l.purchase_invoice_id=? ORDER BY r.seq", (self.invoice_id,),
            ):
                reels[line_id].append(length)
            self.lines = [
                PurchaseLine(product_id, product, unit_id, unit, quantity, price, tuple(reels.get(line_id, ())))
                for line_id, product_id, product, unit_id, unit, quantity, price in conn.execute(
                    "SELECT l.id,p.id,p.code||' — '||p.name_ar,u.id,u.code||' — '||u.name_ar,"
                    "l.quantity_micros,l.unit_price_micros FROM purchase_invoice_lines l "
                    "JOIN products p ON p.id=l.product_id JOIN units u ON u.id=l.unit_id "
                    "WHERE l.purchase_invoice_id=? ORDER BY l.line_no", (self.invoice_id,),
                )
            ]
        return invoice

    def _load_accounts(self, selected: int | None) -> None:
        populate_combo(self.account, financial_account_rows(self.context, selected))
        self.account.setCurrentIndex(self.account.findData(selected) if selected is not None else -1)

    def _add_account(self) -> None:
        if not self.can_add_account:
            return
        dialog = FinancialAccountDialog(self.context, self.user_id, self)
        try:
            if dialog.exec() == QDialog.Accepted:
                self._load_accounts(dialog.account_id)
        finally:
            dialog.deleteLater()

    def _update_payment_fields(self) -> None:
        cash = self.payment.currentData() == "CASH"
        self.has_due.setVisible(not cash)
        self.due_date.setVisible(not cash)
        self.due_date.setEnabled(not self.read_only and self.has_due.isChecked())
        self.account_label.setVisible(cash)
        self.account.setVisible(cash)
        self.add_account.setVisible(cash and self.can_add_account)

    def _refresh_lines(self) -> None:
        fill_table(self.line_table, [
            (
                line.product_name, line.unit_name, format_scaled(line.quantity_micros),
                format_scaled(line.unit_price_micros), len(line.reel_lengths_micros) or "—", money(line.total()),
            )
            for line in self.lines
        ])
        for row, line in enumerate(self.lines):
            if line.reel_lengths_micros:
                self.line_table.item(row, 4).setToolTip("، ".join(map(format_scaled, line.reel_lengths_micros)))
        self._update_totals()

    def _expense_piastres(self) -> int:
        return parse_scaled(self.expense.text().strip() or "0", "المصروف المباشر", 2)

    def _update_totals(self) -> None:
        try:
            expense = self._expense_piastres()
        except ValueError:
            self.totals.setText("راجع مبلغ المصروف المباشر.")
            return
        subtotal = sum(line.total() for line in self.lines)
        self.totals.setText(f"البنود {money(subtotal)}     المصروف {money(expense)}     الإجمالي {money(subtotal + expense)}")

    def _add_line(self) -> None:
        line = self._get_line()
        if line is not None:
            self.lines.append(line)
            self._refresh_lines()
            self.line_table.setCurrentCell(len(self.lines) - 1, 0, SELECT_ROW)

    def _edit_line(self) -> None:
        row = self._selected_line()
        if self.read_only or row is None:
            return
        line = self._get_line(self.lines[row])
        if line is not None:
            self.lines[row] = line
            self._refresh_lines()
            self.line_table.setCurrentCell(row, 0, SELECT_ROW)

    def _get_line(self, line: PurchaseLine | None = None) -> PurchaseLine | None:
        dialog = PurchaseLineDialog(self.context, self, line=line)
        try:
            return dialog.line if dialog.exec() == QDialog.Accepted else None
        finally:
            dialog.deleteLater()

    def _selected_line(self) -> int | None:
        selected = self.line_table.selectionModel().selectedRows()
        return selected[0].row() if selected else None

    def _remove_line(self) -> None:
        row = self._selected_line()
        if not self.read_only and row is not None:
            del self.lines[row]
            self.line_table.clearSelection()
            self._refresh_lines()

    def save(self) -> bool:
        if self.read_only:
            return False
        try:
            self.context.permissions.require(self.user_id, "post_purchases")
            supplier = self.supplier.currentData()
            if supplier is None or not self.lines:
                raise ValueError("اختر موردًا وأضف بندًا واحدًا على الأقل.")
            payment = self.payment.currentData()
            account = self.account.currentData() if payment == "CASH" else None
            if payment == "CASH" and account is None:
                raise ValueError("الشراء النقدي يتطلّب حسابًا ماليًا.")
            fields = {
                "supplier_id": supplier,
                "invoice_date": self.invoice_date.date().toString(Qt.ISODate),
                "lines": [line.values() for line in self.lines],
                "expense_piastres": self._expense_piastres(),
                "payment_type": payment,
                "supplier_invoice_no": self.supplier_reference.text().strip() or None,
                "financial_account_id": account,
                "due_date": self.due_date.date().toString(Qt.ISODate)
                if payment == "CREDIT" and self.has_due.isChecked() else None,
            }
            service = PurchasingService(self.context.db)
            if self.invoice_id is None:
                self.invoice_id = service.create_invoice(**fields, created_by=self.user_id)
            else:
                service.update_invoice(self.invoice_id, **fields, actor_id=self.user_id)
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية إدارة فواتير الشراء.", error=True)
            return False
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر حفظ المسودة. {exc}", error=True)
            return False
        self.accept()
        return True


class PurchasesPage(QWidget):
    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.can_invoice = context.has_permission(user_id, "post_purchases")
        self.can_return = context.has_permission(user_id, "post_purchase_return")
        self.can_pay = context.has_permission(user_id, "manage_supplier_payments")
        self.invoices: list[tuple] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.addWidget(make_title("فواتير الشراء"))

        self.status = QComboBox()
        self.status.addItem("كل الحالات", "")
        for code, label in STATUS_LABELS.items():
            self.status.addItem(label, code)
        self.create_button = QPushButton("فاتورة جديدة")
        self.open_button = QPushButton("فتح الفاتورة")
        self.open_button.setObjectName("Secondary")
        self.post_button = QPushButton("ترحيل المسودة")
        self.post_button.setObjectName("Secondary")
        actions = QHBoxLayout()
        actions.addWidget(self.create_button)
        actions.addWidget(self.open_button)
        actions.addWidget(self.post_button)
        for button in (self.create_button, self.open_button, self.post_button):
            button.setVisible(self.can_invoice)
        actions.addStretch(1)
        actions.addWidget(QLabel("الحالة"))
        actions.addWidget(self.status)
        self.status.setEnabled(self.can_invoice)
        if not self.can_invoice:
            self.status.setCurrentIndex(self.status.findData("POSTED"))
        layout.addLayout(actions)

        settlements = QHBoxLayout()
        self.pay_button = QPushButton("دفعة مورد")
        self.return_button = QPushButton("مرتجع شراء")
        self.settlements_button = QPushButton("تسويات الفاتورة")
        for button, allowed in (
            (self.pay_button, self.can_pay), (self.return_button, self.can_return),
            (self.settlements_button, self.can_pay or self.can_return),
        ):
            button.setObjectName("Secondary")
            button.setVisible(allowed)
            settlements.addWidget(button)
        self.return_button.setToolTip("اختر فاتورة آجلة مرحّلة لإرجاع بضاعة إلى المورد.")
        settlements.addStretch(1)
        layout.addLayout(settlements)

        self.message = make_message()
        layout.addWidget(self.message)
        self.table = make_table(["رقم الفاتورة", "المورد", "التاريخ", "السداد", "الحالة", "الإجمالي"])
        layout.addWidget(self.table, 1)
        self.status.currentIndexChanged.connect(lambda: self.refresh())
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.cellDoubleClicked.connect(self._open_selected)
        self.create_button.clicked.connect(self._new_invoice)
        self.open_button.clicked.connect(self._open_selected)
        self.post_button.clicked.connect(self._confirm_post)
        self.pay_button.clicked.connect(self._pay_supplier)
        self.return_button.clicked.connect(self._return_selected)
        self.settlements_button.clicked.connect(self._show_settlements)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()

    def refresh(self, select_id: int | None = None) -> None:
        status = self.status.currentData()
        self.invoices = query_rows(
            self.context,
            "SELECT p.id,p.document_no,s.name_ar,p.invoice_date,p.payment_type,p.status,p.total_piastres,"
            "p.supplier_id,j.reversed_by_id "
            "FROM purchase_invoices p JOIN suppliers s ON s.id=p.supplier_id "
            "LEFT JOIN journal_entries j ON j.id=p.journal_entry_id "
            "WHERE (?='' OR p.status=?) AND (? OR (p.status='POSTED' AND p.payment_type='CREDIT')) "
            "ORDER BY p.invoice_date DESC,p.id DESC",
            (status, status, self.can_invoice),
        )
        self.table.clearSelection()
        fill_table(self.table, [
            (number, supplier, invoice_date, PAYMENT_LABELS[payment], STATUS_LABELS[state],
             money(total) if self.can_invoice else "—")
            for _, number, supplier, invoice_date, payment, state, total, _, _ in self.invoices
        ])
        for row, invoice in enumerate(self.invoices):
            if invoice[0] == select_id:
                self.table.setCurrentCell(row, 0, SELECT_ROW)
                break
        self._update_actions()

    def _selected(self) -> tuple | None:
        rows = self.table.selectionModel().selectedRows()
        return self.invoices[rows[0].row()] if rows else None

    def _update_actions(self) -> None:
        selected = self._selected()
        self.open_button.setEnabled(self.can_invoice and selected is not None)
        self.post_button.setEnabled(self.can_invoice and selected is not None and selected[5] == "DRAFT")
        self.return_button.setEnabled(
            self.can_return and selected is not None and selected[4:6] == ("CREDIT", "POSTED") and selected[8] is None
        )
        self.settlements_button.setEnabled(selected is not None and selected[5] == "POSTED")

    def _pay_supplier(self) -> None:
        self._open_settlement("manage_supplier_payments")

    def _return_selected(self) -> None:
        if self.return_button.isEnabled():
            self._open_settlement("post_purchase_return")

    def _show_settlements(self) -> None:
        selected = self._selected()
        if selected is None or selected[5] != "POSTED":
            return
        try:
            dialog = PurchaseSettlementHistoryDialog(self.context, self.user_id, selected[0], self)
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية عرض تسويات الفاتورة.", error=True)
        except sqlite3.Error as exc:
            set_message(self.message, f"تعذر عرض تسويات الفاتورة. {exc}", error=True)

    def _open_settlement(self, permission: str) -> None:
        selected = self._selected()
        try:
            self.context.permissions.require(self.user_id, permission)
            if permission == "manage_supplier_payments":
                dialog = SupplierPaymentDialog(
                    self.context, self.user_id, self, supplier_id=selected[7] if selected else None,
                )
            else:
                dialog = PurchaseReturnDialog(self.context, self.user_id, selected[0], self)
            try:
                if dialog.exec() == QDialog.Accepted:
                    self.refresh(selected[0] if selected else None)
                    set_message(self.message, f"تم ترحيل المستند {dialog.result_document}.")
            finally:
                dialog.deleteLater()
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية هذه العملية.", error=True)
        except (ValueError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر فتح الشاشة. {exc}", error=True)

    def _new_invoice(self) -> None:
        self._open_editor()

    def _open_selected(self) -> None:
        selected = self._selected()
        if self.can_invoice and selected is not None:
            self._open_editor(selected[0])

    def _open_editor(self, invoice_id: int | None = None) -> None:
        try:
            self.context.permissions.require(self.user_id, "post_purchases")
            dialog = PurchaseInvoiceDialog(self.context, self.user_id, self, invoice_id=invoice_id)
            try:
                if dialog.exec() == QDialog.Accepted:
                    if self.status.currentData() in ("POSTED", "CANCELLED"):
                        self.status.setCurrentIndex(self.status.findData("DRAFT"))
                    self.refresh(dialog.invoice_id)
                    set_message(self.message, "حُفظت مسودة الشراء.")
            finally:
                dialog.deleteLater()
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية إدارة فواتير الشراء.", error=True)
        except (ValueError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر فتح الفاتورة. {exc}", error=True)

    def _confirm_post(self) -> None:
        selected = self._selected()
        if selected is None or selected[5] != "DRAFT":
            return
        answer = QMessageBox.question(
            self, "ترحيل فاتورة شراء", f"هل تريد ترحيل الفاتورة {selected[1]} إلى المخزون والحسابات؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.post_invoice(selected[0])

    def post_invoice(self, invoice_id: int) -> bool:
        try:
            PurchasingService(self.context.db).post_invoice(invoice_id, actor_id=self.user_id)
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية ترحيل فواتير الشراء.", error=True)
            return False
        except ClosedPeriodError:
            set_message(self.message, "الفترة المحاسبية لتاريخ الفاتورة مقفلة. المسودة محفوظة.", error=True)
            return False
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر ترحيل الفاتورة. {exc}", error=True)
            return False
        if self.status.currentData() == "DRAFT":
            self.status.setCurrentIndex(self.status.findData("POSTED"))
        self.refresh(invoice_id)
        set_message(self.message, "رُحّلت الفاتورة إلى المخزون والحسابات.")
        return True
