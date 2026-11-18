"""Desktop store settings, accounting periods and account administration."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from ..auth import ROLE_DEFS
from ..bootstrap import AppContext
from ..periods import PeriodService
from ..settings import SettingsService
from .common import (
    fill_table, make_date_field, make_message, make_table, make_title, query_rows, set_message,
)
from .financial_accounts import FinancialAccountDialog

SETTINGS = "manage_settings"
USERS = "manage_users"
ROLES = "manage_roles"
STAFF_ROLES = {code: ROLE_DEFS[code] for code in ("CASHIER", "MANAGER")}
ACCOUNT_TYPES = {"CASH": "صندوق", "BANK": "بنك", "WALLET": "محفظة", "OTHER": "أخرى"}
SELECT_ROW = QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows


def _password_field() -> QLineEdit:
    field = QLineEdit()
    field.setEchoMode(QLineEdit.Password)
    return field


def _checked_password(password: QLineEdit, confirm: QLineEdit) -> str:
    value = password.text()
    if len(value) < 8:
        raise ValueError("كلمة المرور يجب ألا تقل عن ٨ أحرف.")
    if value != confirm.text():
        raise ValueError("تأكيد كلمة المرور لا يطابق كلمة المرور.")
    return value


def _error(label: QLabel, exc: Exception) -> None:
    message = "الحساب لا يملك صلاحية هذه العملية." if isinstance(exc, PermissionError) else str(exc)
    set_message(label, message, error=True)


class StoreSettings(QWidget):
    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.name = QLineEdit()
        self.address = QLineEdit()
        self.phone = QLineEdit()
        self.phone.setLayoutDirection(Qt.LeftToRight)
        self.data_path = QLineEdit(str(context.paths.root))
        self.data_path.setReadOnly(True)
        self.data_path.setLayoutDirection(Qt.LeftToRight)
        self.save_button = QPushButton("حفظ بيانات المتجر")
        self.save_button.clicked.connect(self.save)
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("اسم المتجر", self.name)
        form.addRow("العنوان", self.address)
        form.addRow("الهاتف", self.phone)
        form.addRow("مجلد البيانات", self.data_path)
        layout.addLayout(form)
        layout.addWidget(self.save_button, 0, Qt.AlignRight)
        layout.addWidget(self.message)
        layout.addStretch(1)
        rows = query_rows(context, "SELECT name_ar,address,phone FROM store_profile WHERE id=1")
        if rows:
            for field, value in zip((self.name, self.address, self.phone), rows[0]):
                field.setText(value or "")

    def save(self) -> bool:
        try:
            SettingsService(self.context.db).save_store(
                self.name.text(), self.address.text(), self.phone.text(), actor_id=self.user_id,
            )
        except (ValueError, PermissionError, sqlite3.Error) as exc:
            _error(self.message, exc)
            return False
        set_message(self.message, "حُفظت بيانات المتجر للمستندات الجديدة.")
        return True


class FinancialSettings(QWidget):
    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.rows: list[tuple] = []
        self.table = make_table(["الكود", "اسم الحساب", "النوع", "حساب الأستاذ", "الحالة"])
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.add_button = QPushButton("إضافة حساب")
        self.rename_button = QPushButton("تعديل الاسم")
        self.state_button = QPushButton("تعطيل الحساب")
        for button, slot in ((self.add_button, self._add), (self.rename_button, self._rename),
                             (self.state_button, self._change_state)):
            button.clicked.connect(slot)
            if button is not self.add_button:
                button.setObjectName("Secondary")
            bar.addWidget(button)
        bar.addStretch(1)
        layout.addLayout(bar)
        layout.addWidget(self.message)
        layout.addWidget(self.table, 1)
        self.table.itemSelectionChanged.connect(self._actions)
        self.refresh()

    def refresh(self, account_id: int | None = None) -> None:
        self.rows = query_rows(
            self.context,
            "SELECT f.id,f.code,f.name_ar,f.account_type,a.code,f.is_active FROM financial_accounts f "
            "JOIN accounts a ON a.id=f.gl_account_id ORDER BY f.code",
        )
        self.table.clearSelection()
        fill_table(self.table, [
            (code, name, ACCOUNT_TYPES.get(kind, kind), gl, "نشط" if active else "معطّل")
            for _, code, name, kind, gl, active in self.rows
        ])
        for row, account in enumerate(self.rows):
            if account[0] == account_id:
                self.table.setCurrentCell(row, 0, SELECT_ROW)
                break
        self._actions()

    def _selected(self) -> tuple | None:
        rows = self.table.selectionModel().selectedRows()
        return self.rows[rows[0].row()] if rows else None

    def _actions(self) -> None:
        row = self._selected()
        self.rename_button.setEnabled(row is not None)
        self.state_button.setEnabled(row is not None)
        self.state_button.setText("إعادة تفعيل الحساب" if row and not row[5] else "تعطيل الحساب")

    def _add(self) -> None:
        dialog = FinancialAccountDialog(self.context, self.user_id, self)
        try:
            if dialog.exec() == QDialog.Accepted:
                self.refresh(dialog.account_id)
                set_message(self.message, "أُضيف الحساب المالي.")
        finally:
            dialog.deleteLater()

    def _rename(self) -> None:
        row = self._selected()
        if row:
            name, accepted = QInputDialog.getText(self, "تعديل الحساب المالي", "الاسم", text=row[2])
            if accepted:
                self._update(row[0], name=name)

    def _change_state(self) -> None:
        row = self._selected()
        if row and QMessageBox.question(
            self, self.state_button.text(), f"هل تريد {self.state_button.text()} {row[2]}؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            self._update(row[0], active=not row[5])

    def _update(self, account_id: int, **fields) -> None:
        try:
            SettingsService(self.context.db).update_financial_account(account_id, actor_id=self.user_id, **fields)
            self.refresh(account_id)
            set_message(self.message, "حُفظ الحساب المالي.")
        except (ValueError, PermissionError, sqlite3.Error) as exc:
            _error(self.message, exc)


class PeriodSettings(QWidget):
    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.service = PeriodService(context.db)
        self.month = make_date_field()
        self.month.setDisplayFormat("MM/yyyy")
        self.month.setCalendarPopup(False)
        self.status = QLabel()
        self.table = make_table(["الفترات المقفلة"])
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        self.change_button = QPushButton()
        self.change_button.clicked.connect(self._change_period)
        self.closed: set[str] = set()
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("الشهر"))
        bar.addWidget(self.month)
        bar.addWidget(self.status, 1)
        bar.addWidget(self.change_button)
        layout.addLayout(bar)
        layout.addWidget(self.message)
        layout.addWidget(self.table, 1)
        self.month.dateChanged.connect(self._status)
        self.refresh()

    def refresh(self) -> None:
        rows = query_rows(self.context, "SELECT month FROM closed_periods ORDER BY month DESC")
        self.closed = {row[0] for row in rows}
        fill_table(self.table, rows)
        self._status()

    def _status(self) -> None:
        closed = self.month.date().toString("yyyy-MM") in self.closed
        self.status.setText("الفترة مقفلة" if closed else "الفترة مفتوحة")
        self.change_button.setText("إعادة فتح الشهر" if closed else "إقفال الشهر")

    def _change_period(self) -> None:
        month = self.month.date().toString("yyyy-MM")
        reason, accepted = QInputDialog.getText(self, self.change_button.text(), f"السبب للشهر {month}")
        if accepted:
            try:
                action = self.service.reopen_month if month in self.closed else self.service.close_month
                action(month, actor_id=self.user_id, reason=reason)
                self.refresh()
                set_message(self.message, "حُفظت حالة الفترة المحاسبية.")
            except (ValueError, PermissionError, sqlite3.Error) as exc:
                _error(self.message, exc)


class StaffDialog(QDialog):
    def __init__(self, context: AppContext, user_id: int, parent=None, *, reset_id: int | None = None):
        super().__init__(parent)
        self.context = context
        self.user_id = user_id
        self.reset_id = reset_id
        self.staff_id = reset_id
        self.setMinimumWidth(430)
        self.setWindowTitle("إعادة ضبط كلمة المرور" if reset_id is not None else "إضافة مستخدم")
        form = QFormLayout(self)
        if reset_id is None:
            self.name = QLineEdit()
            self.username = QLineEdit()
            self.role = QComboBox()
            self.role.addItem(STAFF_ROLES["CASHIER"], "CASHIER")
            if context.has_permission(user_id, ROLES):
                self.role.addItem(STAFF_ROLES["MANAGER"], "MANAGER")
            form.addRow("الاسم الظاهر", self.name)
            form.addRow("اسم الدخول", self.username)
            form.addRow("الدور", self.role)
        self.password = _password_field()
        self.confirm = _password_field()
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        self.save_button = QPushButton("حفظ")
        self.save_button.clicked.connect(self.save)
        form.addRow("كلمة المرور", self.password)
        form.addRow("تأكيد كلمة المرور", self.confirm)
        form.addRow(self.message)
        form.addRow(self.save_button)

    def save(self) -> bool:
        try:
            password = _checked_password(self.password, self.confirm)
            if self.reset_id is None:
                self.staff_id = self.context.auth.create_user(
                    self.username.text(), self.name.text(), password, [self.role.currentData()], actor_id=self.user_id,
                )
            else:
                self.context.auth.reset_staff_password(self.reset_id, password, actor_id=self.user_id)
        except (ValueError, PermissionError, sqlite3.Error) as exc:
            _error(self.message, exc)
            return False
        self.password.clear()
        self.confirm.clear()
        self.accept()
        return True


class UserSettings(QWidget):
    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.can_manage = context.has_permission(user_id, USERS)
        self.can_assign = context.has_permission(user_id, ROLES)
        self.rows: list[tuple] = []
        self.table = make_table(["اسم الدخول", "الاسم الظاهر", "الأدوار", "الحالة"])
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.add_button = QPushButton("إضافة مستخدم")
        self.role_button = QPushButton("تغيير الدور")
        self.password_button = QPushButton("ضبط كلمة المرور")
        self.state_button = QPushButton("تعطيل المستخدم")
        for button, permission, slot in (
            (self.add_button, self.can_manage, self._add),
            (self.role_button, self.can_assign, self._change_role),
            (self.password_button, self.can_manage, self._reset_password),
            (self.state_button, self.can_manage, self._change_state),
        ):
            button.clicked.connect(slot)
            button.setVisible(permission)
            if button is not self.add_button:
                button.setObjectName("Secondary")
            bar.addWidget(button)
        bar.addStretch(1)
        layout.addLayout(bar)
        layout.addWidget(self.message)
        layout.addWidget(self.table, 1)
        self.table.itemSelectionChanged.connect(self._actions)
        self.refresh()

    def refresh(self, selected_id: int | None = None) -> None:
        self.rows = query_rows(
            self.context,
            "SELECT u.id,u.username,u.display_name,GROUP_CONCAT(r.name_ar,'، '),u.is_active,"
            "MAX(CASE WHEN r.code='OWNER' THEN 1 ELSE 0 END) AS is_owner "
            "FROM users u LEFT JOIN user_roles ur ON ur.user_id=u.id LEFT JOIN roles r ON r.id=ur.role_id "
            "GROUP BY u.id ORDER BY is_owner DESC,u.username",
        )
        self.table.clearSelection()
        fill_table(self.table, [
            (username, name, roles or "—", "نشط" if active else "معطّل")
            for _, username, name, roles, active, _ in self.rows
        ])
        for row, user in enumerate(self.rows):
            if user[0] == selected_id:
                self.table.setCurrentCell(row, 0, SELECT_ROW)
                break
        self._actions()

    def _selected(self) -> tuple | None:
        rows = self.table.selectionModel().selectedRows()
        return self.rows[rows[0].row()] if rows else None

    def _actions(self) -> None:
        user = self._selected()
        staff = user is not None and not user[5] and user[0] != self.user_id
        self.role_button.setEnabled(staff and self.can_assign)
        self.password_button.setEnabled(staff and self.can_manage)
        self.state_button.setEnabled(staff and self.can_manage)
        self.state_button.setText("إعادة تفعيل المستخدم" if user and not user[4] else "تعطيل المستخدم")

    def _add(self) -> None:
        self._staff_dialog()

    def _staff_dialog(self, staff_id: int | None = None) -> None:
        dialog = StaffDialog(self.context, self.user_id, self, reset_id=staff_id)
        try:
            if dialog.exec() == QDialog.Accepted:
                self.refresh(dialog.staff_id)
                set_message(self.message, "تم حفظ حساب الموظف.")
        finally:
            dialog.deleteLater()

    def _reset_password(self) -> None:
        user = self._selected()
        if user and self.password_button.isEnabled():
            self._staff_dialog(user[0])

    def _change_role(self) -> None:
        user = self._selected()
        if not user or not self.role_button.isEnabled():
            return
        labels = list(STAFF_ROLES.values())
        current = labels.index(user[3]) if user[3] in labels else 0
        choice, accepted = QInputDialog.getItem(self, "تغيير دور المستخدم", user[2], labels, current, False)
        if accepted:
            try:
                code = next(code for code, label in STAFF_ROLES.items() if label == choice)
                self.context.auth.assign_staff_role(user[0], code, actor_id=self.user_id)
                self.refresh(user[0])
                set_message(self.message, "حُفظ دور المستخدم.")
            except (ValueError, PermissionError, sqlite3.Error) as exc:
                _error(self.message, exc)

    def _change_state(self) -> None:
        user = self._selected()
        if user and self.state_button.isEnabled() and QMessageBox.question(
            self, self.state_button.text(), f"هل تريد {self.state_button.text()} {user[2]}؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            try:
                self.context.auth.set_staff_active(user[0], not user[4], actor_id=self.user_id)
                self.refresh(user[0])
                set_message(self.message, "حُفظت حالة المستخدم.")
            except (ValueError, PermissionError, sqlite3.Error) as exc:
                _error(self.message, exc)


class AccountSettings(QWidget):
    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.current = _password_field()
        self.password = _password_field()
        self.confirm = _password_field()
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        self.save_button = QPushButton("تغيير كلمة المرور")
        self.save_button.clicked.connect(self.save)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        username = QLabel(context.db.scalar("SELECT username FROM users WHERE id=?", (user_id,)) or "")
        username.setTextFormat(Qt.PlainText)
        form.addRow("اسم الدخول", username)
        form.addRow("كلمة المرور الحالية", self.current)
        form.addRow("كلمة المرور الجديدة", self.password)
        form.addRow("تأكيد كلمة المرور", self.confirm)
        layout.addLayout(form)
        layout.addWidget(self.save_button, 0, Qt.AlignRight)
        layout.addWidget(self.message)
        layout.addStretch(1)

    def save(self) -> bool:
        try:
            password = _checked_password(self.password, self.confirm)
            self.context.auth.change_password(self.user_id, self.current.text(), password)
        except (ValueError, PermissionError, sqlite3.Error) as exc:
            if str(exc) == "current password is incorrect":
                set_message(self.message, "كلمة المرور الحالية غير صحيحة.", error=True)
            else:
                _error(self.message, exc)
            return False
        for field in (self.current, self.password, self.confirm):
            field.clear()
        set_message(self.message, "تغيّرت كلمة المرور.")
        return True


class SettingsPage(QWidget):
    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        permissions = context.permissions.permissions(user_id)
        self.store = self.finance = self.periods = self.users = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.addWidget(make_title("الإعدادات"))
        self.tabs = QTabWidget()
        if SETTINGS in permissions:
            self.store = StoreSettings(context, user_id)
            self.finance = FinancialSettings(context, user_id)
            self.periods = PeriodSettings(context, user_id)
            self.tabs.addTab(self.store, "بيانات المتجر")
            self.tabs.addTab(self.finance, "الحسابات المالية")
            self.tabs.addTab(self.periods, "الفترات المحاسبية")
        if permissions.intersection({USERS, ROLES}):
            self.users = UserSettings(context, user_id)
            self.tabs.addTab(self.users, "المستخدمون")
        self.account = AccountSettings(context, user_id)
        self.tabs.addTab(self.account, "حسابي")
        layout.addWidget(self.tabs, 1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        for page in (self.finance, self.periods, self.users):
            if page is not None:
                try:
                    page.refresh()
                except sqlite3.Error as exc:
                    _error(page.message, exc)
