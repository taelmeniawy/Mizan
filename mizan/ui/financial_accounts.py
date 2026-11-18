"""Financial account setup shared by document entry screens."""

from __future__ import annotations

import sqlite3

from PySide6.QtWidgets import QComboBox, QDialog, QFormLayout, QLineEdit, QPushButton

from ..bootstrap import AppContext
from .common import make_message, query_rows, set_message


def financial_account_rows(context: AppContext, include_id: int | None = None,
                           *, account_type: str | None = None) -> list[tuple]:
    return query_rows(
        context,
        "SELECT f.id,f.code||' — '||f.name_ar FROM financial_accounts f "
        "JOIN accounts a ON a.id=f.gl_account_id "
        "WHERE (f.is_active=1 AND a.account_type='ASSET' AND a.is_control=0 "
        "AND (? IS NULL OR f.account_type=?)) OR f.id=? ORDER BY f.code",
        (account_type, account_type, include_id),
    )


class FinancialAccountDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, parent=None):
        super().__init__(parent)
        self.context = context
        self.user_id = user_id
        self.account_id: int | None = None
        self.setWindowTitle("إضافة حساب مالي")
        self.setMinimumWidth(420)

        self.code = QLineEdit()
        self.name = QLineEdit()
        self.kind = QComboBox()
        self.kind.addItem("صندوق نقدي", ("CASH", "1010"))
        self.kind.addItem("حساب بنكي", ("BANK", "1020"))
        self.kind.addItem("محفظة إلكترونية", ("WALLET", "1030"))
        self.message = make_message()
        self.save_button = QPushButton("إضافة الحساب")
        self.save_button.clicked.connect(self.save)

        form = QFormLayout(self)
        form.addRow("كود الحساب", self.code)
        form.addRow("اسم الحساب", self.name)
        form.addRow("النوع", self.kind)
        form.addRow(self.message)
        form.addRow(self.save_button)

    def save(self) -> bool:
        code, name = self.code.text().strip(), self.name.text().strip()
        try:
            if not code or not name:
                raise ValueError("أدخل كود الحساب واسمه.")
            kind, gl_code = self.kind.currentData()
            self.account_id = self.context.accounting.create_financial_account(
                code, name, kind, gl_code, actor_id=self.user_id,
            )
        except PermissionError:
            set_message(self.message, "إضافة الحسابات المالية تتطلب صلاحية الإعدادات.", error=True)
            return False
        except sqlite3.IntegrityError:
            set_message(self.message, "كود الحساب المالي مستخدم بالفعل.", error=True)
            return False
        except (ValueError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر إضافة الحساب المالي. {exc}", error=True)
            return False
        self.accept()
        return True
