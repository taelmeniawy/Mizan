"""Application shell: login, permission-aware navigation and pages."""

from __future__ import annotations

from contextlib import closing

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..bootstrap import AppContext
from .audit import AuditPage
from .backup import BackupPage
from .common import make_subtitle, make_title
from .dashboard import DashboardPage
from .diagnostics import DiagnosticsPage
from .login import LoginPage
from .master import MasterDataPage

# key, Arabic label, permission or permissions (any one grants the page)
NAV_ITEMS = (
    ("dashboard", "لوحة التشغيل", "view_dashboard"),
    ("master", "البيانات الأساسية", (
        "manage_products", "manage_customers", "manage_suppliers", "manage_projects",
    )),
    ("backup", "النسخ الاحتياطي", "backup_database"),
    ("audit", "سجل التدقيق", "view_audit_log"),
    ("diagnostics", "سلامة البيانات", "backup_database"),
)


class Shell(QWidget):
    logout = Signal()

    def __init__(self, context: AppContext, user_id: int, display_name: str):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.pages: dict[str, QWidget] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(236)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 0, 12)
        side.setSpacing(0)
        brand = QLabel("ميزان")
        brand.setObjectName("Brand")
        brand_hint = QLabel("إدارة المتجر والمحاسبة")
        brand_hint.setObjectName("BrandHint")
        side.addWidget(brand)
        side.addWidget(brand_hint)

        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setFocusPolicy(Qt.NoFocus)
        side.addWidget(self.nav, 1)

        user_label = QLabel(display_name)
        user_label.setObjectName("SidebarUser")
        role_label = QLabel(self._role_names())
        role_label.setObjectName("SidebarRole")
        self.logout_button = QPushButton("تسجيل الخروج")
        self.logout_button.setObjectName("Secondary")
        self.logout_button.clicked.connect(self.logout)
        logout_row = QHBoxLayout()
        logout_row.setContentsMargins(14, 0, 14, 0)
        logout_row.addWidget(self.logout_button)
        side.addWidget(user_label)
        side.addWidget(role_label)
        side.addLayout(logout_row)
        layout.addWidget(sidebar)

        self.content = QStackedWidget()
        for key, title, permission in NAV_ITEMS:
            permissions = permission if isinstance(permission, tuple) else (permission,)
            if not any(context.has_permission(user_id, item) for item in permissions):
                continue
            page = self._build_page(key)
            self.pages[key] = page
            self.content.addWidget(page)
            self.nav.addItem(title)
        self.nav.currentRowChanged.connect(self.content.setCurrentIndex)
        if self.nav.count():
            self.nav.setCurrentRow(0)
        else:
            self.content.addWidget(self._message_page(
                "لا توجد شاشات متاحة لهذا الحساب. راجع صلاحيات الدور مع المالك."
            ))
        layout.addWidget(self.content, 1)

    # ------------------------------------------------------------------
    def _role_names(self) -> str:
        with closing(self.context.db.connect()) as conn:
            rows = conn.execute(
                "SELECT r.name_ar FROM roles r JOIN user_roles ur ON ur.role_id = r.id "
                "WHERE ur.user_id = ? ORDER BY r.code",
                (self.user_id,),
            ).fetchall()
        return " • ".join(row["name_ar"] for row in rows)

    def _build_page(self, key: str) -> QWidget:
        try:
            if key == "dashboard":
                return DashboardPage(self.context)
            if key == "master":
                return MasterDataPage(self.context, self.user_id)
            if key == "backup":
                return BackupPage(self.context, self.user_id)
            if key == "audit":
                return AuditPage(self.context, self.user_id)
            if key == "diagnostics":
                return DiagnosticsPage(self.context)
        except Exception as exc:  # One broken screen must not take the shell down.
            return self._message_page(f"تعذر فتح الشاشة: {exc}")
        return self._message_page("شاشة غير معروفة.")

    def _message_page(self, text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.addWidget(make_title("ميزان"))
        layout.addWidget(make_subtitle(text))
        layout.addStretch(1)
        return page


class MainWindow(QMainWindow):
    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        self.shell: Shell | None = None
        self.user_id: int | None = None
        self.setWindowTitle("ميزان — إدارة المتجر")
        self.resize(1240, 800)
        self.setMinimumSize(1000, 640)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.login = LoginPage(context)
        self.login.logged_in.connect(self.login_succeeded)
        self.stack.addWidget(self.login)
        self.statusBar().showMessage(self._startup_message())

    def _startup_message(self) -> str:
        if self.context.startup.get("ok"):
            return f"فحص البدء: سليم • البيانات: {self.context.paths.root}"
        return "فحص البدء: توجد مشكلات — راجع شاشة «سلامة البيانات» قبل المتابعة."

    def login_succeeded(self, user_id: int) -> None:
        display_name = self.context.db.scalar(
            "SELECT display_name FROM users WHERE id = ?", (user_id,)
        ) or ""
        self.user_id = int(user_id)
        self.shell = Shell(self.context, int(user_id), display_name)
        self.shell.logout.connect(self.logout)
        self.stack.addWidget(self.shell)
        self.stack.setCurrentWidget(self.shell)
        self.statusBar().showMessage(f"المستخدم: {display_name} • البيانات: {self.context.paths.root}")

    def logout(self) -> None:
        if self.shell is not None:
            self.stack.removeWidget(self.shell)
            self.shell.deleteLater()
            self.shell = None
        self.user_id = None
        self.login.reset()
        self.stack.setCurrentWidget(self.login)
        self.statusBar().showMessage(self._startup_message())
