"""Customer collections, sales returns and receivable history."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from ..bootstrap import AppContext
from ..lcp import return_cost
from ..periods import ClosedPeriodError
from ..sales import SalesService
from .common import (
    amount_entries, fill_table, format_scaled, make_date_field, make_message, make_table,
    money, parse_scaled, populate_combo, query_rows, set_message,
)
from .financial_accounts import FinancialAccountDialog, financial_account_rows


def _customer_combo(context: AppContext, customer_id: int | None, *, active_only: bool) -> QComboBox:
    combo = QComboBox()
    combo.setPlaceholderText("اختر العميل")
    populate_combo(combo, query_rows(
        context, "SELECT id,code||' — '||name_ar FROM customers WHERE (?=0 OR is_active=1) ORDER BY code",
        (active_only,),
    ))
    combo.setCurrentIndex(combo.findData(customer_id))
    return combo


def _buttons(dialog: QDialog, layout: QVBoxLayout, text: str | None = None) -> QPushButton | None:
    buttons = QDialogButtonBox()
    save = buttons.addButton(text, QDialogButtonBox.AcceptRole) if text is not None else None
    buttons.addButton("إغلاق", QDialogButtonBox.RejectRole).setObjectName("Secondary")
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return save


class CustomerReceiptDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, parent=None, *, customer_id: int | None = None):
        super().__init__(parent)
        self.context = context
        self.user_id = user_id
        self.service = SalesService(context.db)
        self.document_no: str | None = None
        self.setWindowTitle("تحصيل عميل")
        self.resize(750, 540)
        self.customer = _customer_combo(context, customer_id, active_only=True)
        self.account = QComboBox()
        self.account.setPlaceholderText("اختر الحساب")
        populate_combo(self.account, financial_account_rows(context))
        self.account.setCurrentIndex(-1)
        self.date = make_date_field()
        self.amount = QLineEdit()
        self.amount.setLayoutDirection(Qt.LeftToRight)
        self.notes = QLineEdit()
        self.table = make_table(["الفاتورة", "تاريخها", "المستحق", "المبلغ المخصص بالجنيه"])
        self.fields: list[QLineEdit] = []
        self.invoices: list[dict] = []
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("العميل", self.customer)
        account_row = QHBoxLayout()
        account_row.addWidget(self.account, 1)
        if context.has_permission(user_id, "manage_settings"):
            add = QPushButton("إضافة حساب")
            add.setObjectName("Secondary")
            add.clicked.connect(self._add_account)
            account_row.addWidget(add)
        form.addRow("حساب التحصيل", account_row)
        form.addRow("التاريخ", self.date)
        form.addRow("المبلغ المحصّل بالجنيه", self.amount)
        form.addRow("المرجع أو الملاحظات", self.notes)
        layout.addLayout(form)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.message)
        self.save_button = _buttons(self, layout, "ترحيل التحصيل")
        self.save_button.clicked.connect(self.save)
        self.customer.currentIndexChanged.connect(self.refresh)
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
        customer = self.customer.currentData()
        self.invoices = []
        try:
            if customer is not None:
                self.invoices = self.service.receipt_candidates(customer, self.date.date().toString(Qt.ISODate))
        except (ValueError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر تحميل الفواتير المستحقة. {exc}", error=True)
        self.fields = amount_entries(self.table, [
            (row["document_no"], row["invoice_date"][:10], money(row["outstanding"]), "")
            for row in self.invoices
        ])

    def save(self) -> bool:
        try:
            self.context.permissions.require(self.user_id, "manage_customer_receipts")
            customer, account = self.customer.currentData(), self.account.currentData()
            if customer is None or account is None:
                raise ValueError("اختر العميل وحساب التحصيل.")
            total = parse_scaled(self.amount.text(), "المبلغ المحصّل", 2)
            allocations = []
            for invoice, field in zip(self.invoices, self.fields):
                amount = parse_scaled(field.text().strip() or "0", "المبلغ المخصص", 2)
                if amount > invoice["outstanding"]:
                    raise ValueError(f"مبلغ الفاتورة {invoice['document_no']} أكبر من المستحق.")
                if amount:
                    allocations.append({"sales_id": invoice["sales_id"], "amount_piastres": amount})
            allocated = sum(row["amount_piastres"] for row in allocations)
            if not total or allocated > total:
                raise ValueError("أدخل مبلغ تحصيل موجبًا يغطي المبالغ المخصصة للفواتير.")
            text = f"ترحيل تحصيل {money(total)}، منه {money(allocated)} للفواتير"
            if total > allocated:
                text += f" و{money(total - allocated)} رصيد دائن للعميل"
            if QMessageBox.question(
                self, "تأكيد التحصيل", text + "؟", QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return False
            result = self.service.record_customer_receipt(
                customer, account, total, self.date.date().toString(Qt.ISODate), allocations,
                notes=self.notes.text().strip() or None, actor_id=self.user_id,
            )
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية تحصيلات العملاء.", error=True)
            return False
        except ClosedPeriodError:
            set_message(self.message, "الفترة المحاسبية لتاريخ التحصيل مقفلة.", error=True)
            return False
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر ترحيل التحصيل. {exc}", error=True)
            return False
        self.document_no = result["document_no"]
        self.accept()
        return True


class SaleReturnDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, invoice_id: int, parent=None):
        super().__init__(parent)
        self.context = context
        self.user_id = user_id
        self.invoice_id = invoice_id
        self.service = SalesService(context.db)
        self.document_no: str | None = None
        context.permissions.require(user_id, "post_sales_return")
        invoices = query_rows(
            context,
            "SELECT s.document_no,s.invoice_date,c.name_ar FROM sales s JOIN customers c ON c.id=s.customer_id "
            "WHERE s.id=? AND s.status='POSTED' AND s.reversal_journal_entry_id IS NULL", (invoice_id,),
        )
        if not invoices:
            raise ValueError("اختر فاتورة بيع مرحّلة.")
        number, invoice_date, customer = invoices[0]
        self.setWindowTitle(f"مرتجع بيع — {number}")
        self.resize(840, 560)
        self.date = make_date_field()
        self.date.setMinimumDate(QDate.fromString(invoice_date[:10], Qt.ISODate))
        self.reason = QLineEdit()
        self.method = QComboBox()
        self.method.addItem("تخفيض الذمة وإضافة الفائض لرصيد العميل", "REDUCE_RECEIVABLE")
        if context.has_permission(user_id, "issue_customer_refund"):
            self.method.addItem("رد الفائض نقدًا", "CASH_REFUND")
            self.method.addItem("رد الفائض بنكيًا", "BANK_REFUND")
            self.method.addItem("رد جزء وإضافة الباقي لرصيد العميل", "MIXED")
        self.account = QComboBox()
        self.account.setPlaceholderText("اختر حساب الرد")
        self.refund = QLineEdit()
        self.refund.setLayoutDirection(Qt.LeftToRight)
        self.reference = QLineEdit()
        self.table = make_table(["البند", "الصنف", "المصدر الأصلي", "الوحدة", "المتبقي", "الحالة", "كمية المرتجع"])
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)

        layout = QVBoxLayout(self)
        self.form = QFormLayout()
        reference = QLabel(f"{number} • {customer}")
        reference.setTextFormat(Qt.PlainText)
        self.form.addRow("الفاتورة الأصلية", reference)
        self.form.addRow("تاريخ المرتجع", self.date)
        self.form.addRow("السبب", self.reason)
        self.form.addRow("التسوية", self.method)
        self.form.addRow("حساب الرد", self.account)
        self.form.addRow("مبلغ الرد بالجنيه", self.refund)
        self.form.addRow("مرجع الرد", self.reference)
        layout.addLayout(self.form)
        layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("Subtitle")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addWidget(self.message)
        self.save_button = _buttons(self, layout, "ترحيل المرتجع")
        self.save_button.clicked.connect(self.save)

        self.sources = self.service.sales_return_sources(invoice_id)
        self.outstanding = self.service.invoice_balance(invoice_id)
        self.source_by_line = {source["line_id"]: source for source in self.sources}
        self.fields = amount_entries(self.table, [
            (source["line_no"], source["product"], source["source"],
             source["base_unit"], format_scaled(source["remaining"]), "", "")
            for source in self.sources
        ])
        self.conditions = []
        for row, _ in enumerate(self.sources):
            condition = QComboBox()
            condition.addItem("صالح للبيع", "SELLABLE")
            condition.addItem("تالف", "DAMAGED")
            self.table.setCellWidget(row, 5, condition)
            self.conditions.append(condition)
        self.method.currentIndexChanged.connect(self._refund_fields)
        for field, condition in zip(self.fields, self.conditions):
            field.textChanged.connect(self._preview)
            condition.currentIndexChanged.connect(self._preview)
        self._refund_fields()
        self._preview()
        if not self.sources:
            self.message.setText("كل الكميات المتاحة للإرجاع من هذه الفاتورة تم ردّها.")
            self.save_button.setEnabled(False)

    def _refund_fields(self) -> None:
        method = self.method.currentData()
        refunding = method != "REDUCE_RECEIVABLE"
        for widget in (self.account, self.refund, self.reference):
            widget.setVisible(refunding)
            self.form.labelForField(widget).setVisible(refunding)
        if refunding:
            account_type = {"CASH_REFUND": "CASH", "BANK_REFUND": "BANK"}.get(method)
            populate_combo(self.account, financial_account_rows(self.context, account_type=account_type))
            self.account.setCurrentIndex(-1)

    def _return_lines(self) -> list[dict]:
        groups: dict[tuple[int, str], tuple[dict, list[dict]]] = {}
        for source, field, condition in zip(self.sources, self.fields, self.conditions):
            quantity = parse_scaled(field.text().strip() or "0", "كمية المرتجع", 6)
            if quantity > source["remaining"]:
                raise ValueError(f"الكمية أكبر من المتبقي في {source['source']}.")
            if quantity:
                key = (source["line_id"], condition.currentData())
                _, selections = groups.setdefault(key, (source, []))
                selections.append({
                    "kind": source["kind"], "allocation_id": source["allocation_id"], "quantity_micros": quantity,
                })
        lines = []
        for (line_id, condition), (source, allocations) in groups.items():
            base = sum(row["quantity_micros"] for row in allocations)
            quantity, remainder = divmod(base * source["conversion_den"], source["conversion_num"])
            if remainder:
                raise ValueError(f"كمية البند {source['line_no']} لا تمثل كمية قابلة للتسجيل بوحدة البيع الأصلية.")
            lines.append({
                "sales_line_id": line_id, "quantity_micros": quantity, "condition": condition,
                "allocation_selections": allocations,
            })
        return lines

    def _preview(self) -> None:
        try:
            quantities: dict[int, int] = {}
            for row in self._return_lines():
                line_id = row["sales_line_id"]
                quantities[line_id] = quantities.get(line_id, 0) + row["quantity_micros"]
            total = 0
            for line_id, quantity in quantities.items():
                source = self.source_by_line[line_id]
                previous = source["previous_returns"]
                total += return_cost(source["net_piastres"], source["sold_quantity"], previous, previous + quantity)
            reduction = min(total, self.outstanding)
            self.summary.setText(
                f"قيمة المرتجع {money(total)}     تخفيض الذمة {money(reduction)}     الفائض {money(total - reduction)}"
            )
        except ValueError as exc:
            self.summary.setText(str(exc))

    def save(self) -> bool:
        try:
            self.context.permissions.require(self.user_id, "post_sales_return")
            lines = self._return_lines()
            reason = self.reason.text().strip()
            if not lines or not reason:
                raise ValueError("أدخل كمية واحدة على الأقل وسبب المرتجع.")
            method = self.method.currentData()
            refund, account, reference = 0, None, None
            if method != "REDUCE_RECEIVABLE":
                self.context.permissions.require(self.user_id, "issue_customer_refund")
                refund = parse_scaled(self.refund.text(), "مبلغ الرد", 2)
                account, reference = self.account.currentData(), self.reference.text().strip()
                if not refund or account is None or not reference:
                    raise ValueError("أدخل مبلغ الرد وحسابه ومرجعه.")
            text = "ترحيل المرتجع وتسوية حساب العميل"
            if refund:
                text += f" وردّ {money(refund)}"
            if QMessageBox.question(
                self, "تأكيد المرتجع", text + "؟", QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return False
            result = self.service.record_sales_return(
                self.invoice_id, lines, self.date.date().toString(Qt.ISODate), reason,
                settlement_method=method, financial_account_id=account, refund_piastres=refund,
                refund_reference=reference, actor_id=self.user_id,
            )
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية المرتجع أو ردّ المبلغ.", error=True)
            return False
        except ClosedPeriodError:
            set_message(self.message, "الفترة المحاسبية لتاريخ المرتجع مقفلة.", error=True)
            return False
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر ترحيل المرتجع. {exc}", error=True)
            return False
        self.document_no = result["document_no"]
        self.accept()
        return True


class CustomerAccountDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, parent=None, *, customer_id: int | None = None):
        super().__init__(parent)
        context.permissions.require(user_id, "view_customer_balance")
        self.service = SalesService(context.db)
        self.setWindowTitle("كشف حساب العميل")
        self.resize(825, 490)
        self.customer = _customer_combo(context, customer_id, active_only=False)
        self.table = make_table(["التاريخ", "المستند", "الحساب", "مدين", "دائن", "ذمة العميل", "الرصيد الدائن"])
        self.balance = make_message()
        self.balance.setTextFormat(Qt.PlainText)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("العميل", self.customer)
        layout.addLayout(form)
        layout.addWidget(self.balance)
        layout.addWidget(self.table, 1)
        _buttons(self, layout)
        self.customer.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        customer = self.customer.currentData()
        self.balance.clear()
        rows = []
        if customer is not None:
            try:
                rows = self.service.customer_statement(customer)
                ar = rows[-1]["ar_balance"] if rows else 0
                credit = rows[-1]["credit_balance"] if rows else 0
                set_message(self.balance, f"المستحق {money(ar)}     رصيد دائن للعميل {money(credit)}")
            except (ValueError, sqlite3.Error) as exc:
                set_message(self.balance, f"تعذر عرض حساب العميل. {exc}", error=True)
        fill_table(self.table, [
            (row["entry_date"][:10], row["document_no"] or row["entry_no"],
             "ذمم" if row["affects_ar"] else "رصيد دائن",
             money(row["debit"]), money(row["credit"]), money(row["ar_balance"]), money(row["credit_balance"]))
            for row in rows
        ])


class ApplyCustomerCreditDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, invoice_id: int, parent=None):
        super().__init__(parent)
        context.permissions.require(user_id, "manage_customer_receipts")
        self.user_id = user_id
        self.invoice_id = invoice_id
        self.service = SalesService(context.db)
        self.feedback = "طُبّق رصيد العميل على الفاتورة."
        invoices = query_rows(
            context, "SELECT document_no,customer_id,invoice_date FROM sales WHERE id=?", (invoice_id,),
        )
        if not invoices:
            raise ValueError("فاتورة البيع غير موجودة.")
        number, customer, invoice_date = invoices[0]
        self.outstanding = self.service.invoice_balance(invoice_id)
        self.setWindowTitle(f"استخدام رصيد العميل — {number}")
        self.setMinimumWidth(500)
        self.date = make_date_field()
        self.date.setMinimumDate(QDate.fromString(invoice_date[:10], Qt.ISODate))
        self.source = QComboBox()
        self.source.setPlaceholderText("اختر مصدر الرصيد")
        self.sources: dict[int, int] = {}
        rows = query_rows(
            context,
            "SELECT c.id,COALESCE(r.document_no,s.document_no,'#'||c.id),c.remaining_piastres "
            "FROM customer_credits c LEFT JOIN customer_receipts r ON c.source_type='OVERPAYMENT' AND r.id=c.source_id "
            "LEFT JOIN sales_returns s ON c.source_type='SALES_RETURN' AND s.id=c.source_id "
            "WHERE c.customer_id=? AND c.status='OPEN' AND c.remaining_piastres>0 ORDER BY c.id",
            (customer,),
        )
        for credit_id, reference, amount in rows:
            self.source.addItem(f"{reference} • {money(amount)}", credit_id)
            self.sources[credit_id] = amount
        self.source.setCurrentIndex(-1)
        self.amount = QLineEdit()
        self.amount.setLayoutDirection(Qt.LeftToRight)
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("المستحق على الفاتورة", QLabel(money(self.outstanding)))
        form.addRow("مصدر الرصيد الدائن", self.source)
        form.addRow("تاريخ الاستخدام", self.date)
        form.addRow("المبلغ بالجنيه", self.amount)
        layout.addLayout(form)
        layout.addWidget(self.message)
        self.save_button = _buttons(self, layout, "تطبيق الرصيد")
        self.save_button.clicked.connect(self.save)
        if not self.outstanding or not self.sources:
            self.save_button.setEnabled(False)
            self.message.setText("تطبيق الرصيد يتطلب فاتورة مستحقة ورصيدًا دائنًا متاحًا.")

    def save(self) -> bool:
        try:
            credit = self.source.currentData()
            if credit is None:
                raise ValueError("اختر مصدر الرصيد الدائن.")
            amount = parse_scaled(self.amount.text(), "المبلغ", 2)
            if not 0 < amount <= min(self.outstanding, self.sources[credit]):
                raise ValueError("المبلغ يجب أن يكون موجبًا وفي حدود المستحق والرصيد المتاح.")
            if QMessageBox.question(
                self, "تأكيد تطبيق الرصيد", f"هل تريد تطبيق {money(amount)} على الفاتورة؟",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return False
            self.service.apply_customer_credit(
                credit, self.invoice_id, amount, actor_id=self.user_id,
                application_date=self.date.date().toString(Qt.ISODate),
            )
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية تحصيلات العملاء.", error=True)
            return False
        except ClosedPeriodError:
            set_message(self.message, "الفترة المحاسبية لتاريخ استخدام الرصيد مقفلة.", error=True)
            return False
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر تطبيق الرصيد. {exc}", error=True)
            return False
        self.accept()
        return True


class SaleSettlementHistoryDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, invoice_id: int, parent=None):
        super().__init__(parent)
        context.permissions.require(user_id, "view_customer_balance")
        self.setWindowTitle("تسويات فاتورة البيع")
        self.resize(740, 420)
        self.table = make_table(["المستند", "النوع", "التاريخ", "المبلغ"])
        rows = query_rows(
            context,
            "SELECT r.document_no,'RECEIPT',r.receipt_date,a.amount_piastres "
            "FROM customer_receipt_allocations a JOIN customer_receipts r ON r.id=a.receipt_id "
            "WHERE a.sales_id=? AND r.status='POSTED' "
            "UNION ALL SELECT document_no,'RETURN',return_date,commercial_total_piastres "
            "FROM sales_returns WHERE sales_id=? AND status='POSTED' "
            "UNION ALL SELECT j.entry_no,'CREDIT',j.entry_date,a.amount_piastres "
            "FROM customer_credit_applications a JOIN journal_entries j "
            "ON j.source_type='CUSTOMER_CREDIT_APPLICATION' AND j.source_id=a.id AND j.entry_kind='POSTING' "
            "WHERE a.sales_id=? ORDER BY 3 DESC,1 DESC",
            (invoice_id, invoice_id, invoice_id),
        )
        labels = {"RECEIPT": "تحصيل", "RETURN": "مرتجع", "CREDIT": "استخدام رصيد دائن"}
        fill_table(self.table, [(number, labels[kind], day[:10], money(amount))
                               for number, kind, day, amount in rows])
        layout = QVBoxLayout(self)
        if not rows:
            layout.addWidget(QLabel("لا توجد تسويات مرحّلة على هذه الفاتورة."))
        layout.addWidget(self.table, 1)
        _buttons(self, layout)
