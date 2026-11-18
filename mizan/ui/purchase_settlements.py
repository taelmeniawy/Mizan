"""Dialogs for supplier payments and returns against posted purchases."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout,
)
from .financial_accounts import FinancialAccountDialog, financial_account_rows

from ..bootstrap import AppContext
from ..periods import ClosedPeriodError
from ..purchasing import PurchasingService
from .common import (
    amount_entries, fill_table, format_scaled, make_date_field, make_message, make_table, money,
    parse_scaled, populate_combo, query_rows, set_message,
)


def _amounts(fields: list[QLineEdit], label: str, decimal_places: int) -> list[int]:
    return [parse_scaled(field.text().strip() or "0", label, decimal_places) for field in fields]


def _actions(dialog: QDialog, layout, label: str, save) -> None:
    buttons = QDialogButtonBox()
    buttons.addButton(label, QDialogButtonBox.AcceptRole).clicked.connect(save)
    buttons.addButton("إغلاق", QDialogButtonBox.RejectRole).setObjectName("Secondary")
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)


class SupplierPaymentDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, parent=None, *, supplier_id: int | None = None):
        super().__init__(parent)
        self.context = context
        self.user_id = user_id
        self.result_document: str | None = None
        self.setWindowTitle("دفعة مورد")
        self.resize(720, 515)

        self.supplier = QComboBox()
        self.supplier.setPlaceholderText("اختر المورد")
        populate_combo(self.supplier, query_rows(
            context, "SELECT id,code||' — '||name_ar FROM suppliers ORDER BY code",
        ))
        self.supplier.setCurrentIndex(self.supplier.findData(supplier_id))
        self.account = QComboBox()
        self.account.setPlaceholderText("اختر الحساب المالي")
        populate_combo(self.account, financial_account_rows(context))
        self.account.setCurrentIndex(-1)
        self.date = make_date_field()
        self.reference = QLineEdit()
        self.table = make_table(["الفاتورة", "تاريخ الفاتورة", "المستحق", "الدفعة بالجنيه"])
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        self.invoices: list[dict] = []
        self.amount_fields: list[QLineEdit] = []

        layout = QVBoxLayout(self)
        fields = QFormLayout()
        fields.addRow("المورد", self.supplier)
        account_row = QHBoxLayout()
        account_row.addWidget(self.account, 1)
        if self.context.has_permission(user_id, "manage_settings"):
            add_account = QPushButton("إضافة حساب")
            add_account.setObjectName("Secondary")
            add_account.clicked.connect(self._add_account)
            account_row.addWidget(add_account)
        fields.addRow("الحساب المالي", account_row)
        fields.addRow("تاريخ الدفع", self.date)
        fields.addRow("مرجع الدفع", self.reference)
        layout.addLayout(fields)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.message)
        _actions(self, layout, "ترحيل الدفعة", self.save)
        self.supplier.currentIndexChanged.connect(self.refresh)
        self.date.dateChanged.connect(self.refresh)
        self.refresh()

    def _add_account(self) -> None:
        dialog = FinancialAccountDialog(self.context, self.user_id, self)
        try:
            if dialog.exec() == QDialog.Accepted:
                populate_combo(self.account, financial_account_rows(self.context))
                self.account.setCurrentIndex(self.account.findData(dialog.account_id))
        finally:
            dialog.deleteLater()

    def refresh(self) -> None:
        self.message.clear()
        self.invoices = []
        supplier = self.supplier.currentData()
        if supplier is not None:
            try:
                self.invoices = PurchasingService(self.context.db).supplier_payment_candidates(
                    supplier, self.date.date().toString(Qt.ISODate),
                )
                if not self.invoices:
                    self.message.setText("لا توجد فواتير آجلة مستحقة لهذا المورد في التاريخ المحدد.")
            except (ValueError, sqlite3.Error) as exc:
                set_message(self.message, f"تعذر تحميل الفواتير المستحقة. {exc}", error=True)
        self.amount_fields = amount_entries(self.table, [
            (row["document_no"], row["invoice_date"][:10], money(row["outstanding_piastres"]), "")
            for row in self.invoices
        ])

    def save(self) -> bool:
        try:
            self.context.permissions.require(self.user_id, "manage_supplier_payments")
            supplier, account = self.supplier.currentData(), self.account.currentData()
            reference = self.reference.text().strip()
            if supplier is None or account is None or not reference:
                raise ValueError("اختر المورد والحساب المالي وأدخل مرجع الدفع.")
            allocations = []
            for invoice, amount in zip(self.invoices, _amounts(self.amount_fields, "مبلغ الدفعة", 2)):
                if amount > invoice["outstanding_piastres"]:
                    raise ValueError(f"المبلغ المخصص للفاتورة {invoice['document_no']} أكبر من المستحق.")
                if amount:
                    allocations.append({"purchase_invoice_id": invoice["purchase_invoice_id"], "amount_piastres": amount})
            if not allocations:
                raise ValueError("أدخل مبلغًا موجبًا لفاتورة واحدة على الأقل.")
            total = money(sum(row["amount_piastres"] for row in allocations))
            if QMessageBox.question(
                self, "تأكيد دفعة المورد", f"هل تريد ترحيل دفعة بمبلغ {total}؟",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return False
            result = PurchasingService(self.context.db).post_supplier_payment(
                supplier, account, self.date.date().toString(Qt.ISODate), allocations,
                reference=reference, actor_id=self.user_id,
            )
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية تسجيل دفعات الموردين.", error=True)
            return False
        except ClosedPeriodError:
            set_message(self.message, "الفترة المحاسبية لتاريخ الدفعة مقفلة.", error=True)
            return False
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر ترحيل الدفعة. {exc}", error=True)
            return False
        self.result_document = result["document_no"]
        self.accept()
        return True


class PurchaseReturnDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, invoice_id: int, parent=None):
        super().__init__(parent)
        self.context = context
        self.user_id = user_id
        self.invoice_id = invoice_id
        self.result_document: str | None = None
        invoice = query_rows(
            context,
            "SELECT p.document_no,s.name_ar FROM purchase_invoices p JOIN suppliers s ON s.id=p.supplier_id "
            "JOIN journal_entries j ON j.id=p.journal_entry_id "
            "WHERE p.id=? AND p.status='POSTED' AND p.payment_type='CREDIT' AND j.reversed_by_id IS NULL",
            (invoice_id,),
        )
        if not invoice:
            raise ValueError("اختر فاتورة شراء آجلة مرحّلة.")
        number, supplier = invoice[0]
        self.setWindowTitle(f"مرتجع شراء — {number}")
        self.resize(780, 495)
        self.date = make_date_field()
        self.reason = QLineEdit()
        self.table = make_table(["الصنف", "الدفعة", "البكرة", "وحدة الأساس", "المتبقي", "كمية المرتجع"])
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)

        layout = QVBoxLayout(self)
        fields = QFormLayout()
        source = QLabel(f"{number} • {supplier}")
        source.setTextFormat(Qt.PlainText)
        fields.addRow("الفاتورة الأصلية", source)
        fields.addRow("تاريخ المرتجع", self.date)
        fields.addRow("السبب", self.reason)
        layout.addLayout(fields)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.message)
        _actions(self, layout, "ترحيل المرتجع", self.save)

        self.sources = query_rows(
            context,
            "SELECT i.id,l.id,r.id,p.name_ar,l.lot_no,r.reel_no,u.name_ar,SUM(c.remaining_qty_micros) "
            "FROM purchase_invoice_lines i JOIN purchase_lots l ON l.purchase_invoice_line_id=i.id "
            "JOIN products p ON p.id=l.product_id JOIN units u ON u.id=p.base_unit_id "
            "JOIN inventory_cost_layers c ON c.lot_id=l.id LEFT JOIN reels r ON r.id=c.reel_id AND r.lot_id=l.id "
            "WHERE i.purchase_invoice_id=? AND c.status='OPEN' AND c.remaining_qty_micros>0 "
            "AND ((l.tracking_mode='LOT' AND c.reel_id IS NULL) OR "
            "(l.tracking_mode='REEL' AND r.status NOT IN ('DAMAGED','RETURNED'))) "
            "GROUP BY l.id,r.id ORDER BY i.line_no,l.id,r.id", (invoice_id,),
        )
        self.quantity_fields = amount_entries(self.table, [
            (product, lot, reel or "—", unit, format_scaled(remaining), "")
            for _, _, _, product, lot, reel, unit, remaining in self.sources
        ])
        if not self.sources:
            self.message.setText("لا توجد كمية متبقية قابلة للإرجاع من هذه الفاتورة.")

    def save(self) -> bool:
        try:
            self.context.permissions.require(self.user_id, "post_purchase_return")
            reason = self.reason.text().strip()
            if not reason:
                raise ValueError("أدخل سبب المرتجع.")
            lines = []
            for source, quantity in zip(self.sources, _amounts(self.quantity_fields, "كمية المرتجع", 6)):
                line, lot, reel, _, lot_no, reel_no, _, remaining = source
                if quantity > remaining:
                    raise ValueError(f"الكمية أكبر من المتبقي في {reel_no or lot_no}.")
                if quantity:
                    lines.append({
                        "purchase_invoice_line_id": line, "lot_id": lot,
                        "reel_id": reel, "quantity_micros": quantity,
                    })
            if not lines:
                raise ValueError("أدخل كمية مرتجعة واحدة على الأقل.")
            if QMessageBox.question(
                self, "تأكيد المرتجع", "هل تريد ترحيل المرتجع وخصم الكمية من المخزون؟",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return False
            result = PurchasingService(self.context.db).return_credit_purchase(
                self.invoice_id, lines, self.date.date().toString(Qt.ISODate),
                reason=reason, actor_id=self.user_id,
            )
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية ترحيل مرتجعات الشراء.", error=True)
            return False
        except ClosedPeriodError:
            set_message(self.message, "الفترة المحاسبية لتاريخ المرتجع مقفلة.", error=True)
            return False
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر ترحيل المرتجع. {exc}", error=True)
            return False
        self.result_document = result["document_no"]
        self.accept()
        return True


class PurchaseSettlementHistoryDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, invoice_id: int, parent=None):
        super().__init__(parent)
        can_pay = context.has_permission(user_id, "manage_supplier_payments")
        can_return = context.has_permission(user_id, "post_purchase_return")
        if not (can_pay or can_return):
            raise PermissionError("missing settlement permission")
        self.setWindowTitle("تسويات الفاتورة")
        self.resize(750, 425)
        self.table = make_table(["المستند", "النوع", "التاريخ", "المرجع / السبب", "المبلغ", "الحالة"])
        rows = query_rows(
            context,
            "SELECT p.document_no,'PAYMENT',p.payment_date,p.reference,a.amount_piastres,j.reversed_by_id "
            "FROM supplier_payment_allocations a JOIN supplier_payments p ON p.id=a.payment_id "
            "JOIN journal_entries j ON j.id=p.journal_entry_id WHERE a.purchase_invoice_id=? AND ? "
            "UNION ALL "
            "SELECT r.document_no,'RETURN',r.return_date,r.reason,r.total_piastres,j.reversed_by_id "
            "FROM purchase_returns r JOIN journal_entries j ON j.id=r.journal_entry_id "
            "WHERE r.purchase_invoice_id=? AND r.status='POSTED' AND ? ORDER BY 3 DESC,1 DESC",
            (invoice_id, can_pay, invoice_id, can_return),
        )
        fill_table(self.table, [
            (number, "دفعة" if kind == "PAYMENT" else "مرتجع", day,
             ref or "—", money(amount), "معكوس" if reversal is not None else "مرحّل")
            for number, kind, day, ref, amount, reversal in rows
        ])
        layout = QVBoxLayout(self)
        if not rows:
            layout.addWidget(QLabel("لا توجد دفعات أو مرتجعات مرحّلة متاحة لهذه الفاتورة."))
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox()
        buttons.addButton("إغلاق", QDialogButtonBox.RejectRole).setObjectName("Secondary")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
