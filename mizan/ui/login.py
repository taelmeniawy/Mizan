"""Login and first-run setup screen."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..bootstrap import AppContext, BootstrapError, initialize_first_run
from .common import clear_layout, make_card, make_message, make_subtitle, make_title, set_message


class LoginPage(QWidget):
    logged_in = Signal(int)
    initialized = Signal()

    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(24, 24, 24, 24)
        self.render()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def render(self) -> None:
        clear_layout(self._root)
        first_run = self.context.user_count() == 0
        card = make_card()
        card.setObjectName("LoginCard")
        card.setMinimumWidth(370)
        card.setMaximumWidth(470)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(30, 26, 30, 26)
        inner.setSpacing(9)

        inner.addWidget(make_title("ميزان"))
        inner.addWidget(make_subtitle(
            "نظام المحاسبة والمخزون وإدارة العروض — جهاز واحد، بلا اتصال بالإنترنت."
        ))
        inner.addSpacing(6)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFormAlignment(Qt.AlignRight | Qt.AlignTop)
        form.setSpacing(8)

        self.message = make_message()
        if first_run:
            self._build_setup(inner, form)
        else:
            self.username = QLineEdit()
            self.username.setPlaceholderText("اسم الدخول")
            self.password = QLineEdit()
            self.password.setEchoMode(QLineEdit.Password)
            self.password.setPlaceholderText("كلمة المرور")
            form.addRow("اسم الدخول", self.username)
            form.addRow("كلمة المرور", self.password)
            inner.addLayout(form)
            self.submit_button = QPushButton("دخول")
            self.submit_button.clicked.connect(self.attempt)
            self.password.returnPressed.connect(self.attempt)
            inner.addWidget(self.submit_button)

        inner.addWidget(self.message)
        self._root.addStretch(1)
        self._root.addWidget(card, 0, Qt.AlignHCenter)
        self._root.addStretch(1)
        if first_run:
            self.store_name.setFocus()
        else:
            self.username.setFocus()

    def _build_setup(self, inner: QVBoxLayout, owner_form: QFormLayout) -> None:
        self.steps = QStackedWidget()
        store_page = QWidget()
        store_page.setObjectName("SetupStep")
        store_layout = QVBoxLayout(store_page)
        store_layout.setContentsMargins(0, 0, 0, 0)
        store_layout.addWidget(make_subtitle("١ من ٢ — بيانات المتجر"))
        store_form = QFormLayout()
        self.store_name = QLineEdit()
        self.store_name.setPlaceholderText("اسم المتجر كما سيظهر في المستندات")
        self.address = QLineEdit()
        self.phone = QLineEdit()
        self.phone.setLayoutDirection(Qt.LeftToRight)
        self.create_cash = QCheckBox("إضافة صندوق نقدي")
        self.create_cash.setChecked(True)
        self.cash_name = QLineEdit("الصندوق الرئيسي")
        self.create_cash.toggled.connect(self.cash_name.setEnabled)
        store_form.addRow("اسم المتجر", self.store_name)
        store_form.addRow("العنوان (اختياري)", self.address)
        store_form.addRow("الهاتف (اختياري)", self.phone)
        store_form.addRow(self.create_cash)
        store_form.addRow("اسم الصندوق", self.cash_name)
        store_layout.addLayout(store_form)
        store_layout.addStretch(1)
        self.next_button = QPushButton("التالي")
        self.next_button.clicked.connect(self._next_step)
        store_layout.addWidget(self.next_button)
        self.steps.addWidget(store_page)

        owner_page = QWidget()
        owner_page.setObjectName("SetupStep")
        owner_layout = QVBoxLayout(owner_page)
        owner_layout.setContentsMargins(0, 0, 0, 0)
        owner_layout.addWidget(make_subtitle("٢ من ٢ — حساب المالك"))
        self.display_name = QLineEdit()
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setPlaceholderText("٨ أحرف على الأقل")
        self.password.setEchoMode(QLineEdit.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.Password)
        owner_form.addRow("الاسم الظاهر", self.display_name)
        owner_form.addRow("اسم الدخول", self.username)
        owner_form.addRow("كلمة المرور", self.password)
        owner_form.addRow("تأكيد كلمة المرور", self.confirm)
        owner_layout.addLayout(owner_form)
        owner_layout.addStretch(1)
        buttons = QHBoxLayout()
        self.submit_button = QPushButton("إنشاء الحساب والبدء")
        self.submit_button.clicked.connect(self.create_owner)
        self.confirm.returnPressed.connect(self.create_owner)
        back = QPushButton("رجوع")
        back.setObjectName("Secondary")
        back.clicked.connect(self._previous_step)
        buttons.addWidget(self.submit_button)
        buttons.addWidget(back)
        owner_layout.addLayout(buttons)
        self.steps.addWidget(owner_page)
        inner.addWidget(self.steps)

    def _next_step(self) -> None:
        if not self.store_name.text().strip():
            set_message(self.message, "أدخل اسم المتجر.", error=True)
            return
        if self.create_cash.isChecked() and not self.cash_name.text().strip():
            set_message(self.message, "أدخل اسم الصندوق النقدي.", error=True)
            return
        self.message.clear()
        self.steps.setCurrentIndex(1)
        self.display_name.setFocus()

    def _previous_step(self) -> None:
        self.message.clear()
        self.steps.setCurrentIndex(0)
        self.store_name.setFocus()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def attempt(self) -> bool:
        username = self.username.text().strip()
        password = self.password.text()
        if not username or not password:
            set_message(self.message, "أدخل اسم الدخول وكلمة المرور.", error=True)
            return False
        try:
            user = self.context.auth.login(username, password)
        except ValueError:
            set_message(self.message, "اسم الدخول أو كلمة المرور غير صحيحة.", error=True)
            self.password.clear()
            return False
        set_message(self.message, f"مرحبًا {user['display_name']}.")
        self.password.clear()
        self.logged_in.emit(int(user["id"]))
        return True

    def create_owner(self) -> bool:
        try:
            owner_id = initialize_first_run(
                self.context,
                store_name=self.store_name.text(),
                display_name=self.display_name.text(),
                username=self.username.text(),
                password=self.password.text(),
                confirm_password=self.confirm.text(),
                address=self.address.text(),
                phone=self.phone.text(),
                cash_account_name=self.cash_name.text() if self.create_cash.isChecked() else None,
            )
        except BootstrapError as exc:
            set_message(self.message, _arabic_error(str(exc)), error=True)
            return False
        except (ValueError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر إتمام الإعداد. {exc}", error=True)
            return False
        self.password.clear()
        self.confirm.clear()
        self.initialized.emit()
        self.logged_in.emit(owner_id)
        return True

    def reset(self) -> None:
        self.render()


_ARABIC = {
    "the application already has users": "التطبيق مهيأ بالفعل وله مستخدمون.",
    "store name, display name and username are required": "أدخل اسم المتجر والاسم الظاهر واسم الدخول.",
    "the password must be at least 8 characters": "كلمة المرور يجب ألا تقل عن 8 أحرف.",
    "the password confirmation does not match": "تأكيد كلمة المرور لا يطابق كلمة المرور.",
    "cash account name is required": "أدخل اسم الصندوق النقدي.",
}


def _arabic_error(message: str) -> str:
    return _ARABIC.get(message, message)
