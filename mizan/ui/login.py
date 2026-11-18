"""Login and first-run setup screen."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
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
            self.store_name = QLineEdit()
            self.store_name.setPlaceholderText("اسم المتجر كما سيظهر في المستندات")
            self.display_name = QLineEdit()
            self.display_name.setPlaceholderText("اسمك كما سيظهر في سجل التدقيق")
            self.username = QLineEdit()
            self.username.setPlaceholderText("اسم الدخول")
            self.password = QLineEdit()
            self.password.setEchoMode(QLineEdit.Password)
            self.confirm = QLineEdit()
            self.confirm.setEchoMode(QLineEdit.Password)
            form.addRow("اسم المتجر", self.store_name)
            form.addRow("الاسم الظاهر", self.display_name)
            form.addRow("اسم الدخول", self.username)
            form.addRow("كلمة المرور", self.password)
            form.addRow("تأكيد كلمة المرور", self.confirm)
            inner.addLayout(form)
            self.submit_button = QPushButton("إنشاء الحساب والبدء")
            self.submit_button.clicked.connect(self.create_owner)
            self.confirm.returnPressed.connect(self.create_owner)
            inner.addWidget(self.submit_button)
            inner.addWidget(make_subtitle(
                "هذا الإعداد الأولي يُنشئ حساب المالك الوحيد مرة واحدة. الحد الأدنى لكلمة المرور 8 أحرف."
            ))
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
        self.username.setFocus()

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
            )
        except BootstrapError as exc:
            set_message(self.message, _arabic_error(str(exc)), error=True)
            return False
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
}


def _arabic_error(message: str) -> str:
    return _ARABIC.get(message, message)
