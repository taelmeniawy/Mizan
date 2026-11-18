"""Master-data screens: units, products, customers, suppliers and projects."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..bootstrap import AppContext
from ..master import MasterDataService
from .common import (
    fill_table,
    make_card,
    make_message,
    make_subtitle,
    make_table,
    make_title,
    query_rows,
    set_message,
)
from .pricing import PricingTab

CUSTOMER_TYPES = (("RETAIL", "تجزئة"), ("WHOLESALE", "جملة"), ("CONTRACTOR", "مقاول"))
TRACKING_MODES = (("LOT", "دفعة LOT"), ("REEL", "بكرة REEL"))


class UnitsTab(QWidget):
    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        self.master = MasterDataService(context.db)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        form = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("الكود، مثل PIECE")
        self.name = QLineEdit()
        self.name.setPlaceholderText("اسم الوحدة، مثل قطعة")
        self.allows_fraction = QCheckBox("تسمح بكسور")
        self.add_button = QPushButton("إضافة وحدة")
        self.add_button.clicked.connect(self.add_unit)
        form.addWidget(QLabel("الكود"))
        form.addWidget(self.code)
        form.addWidget(QLabel("الاسم"))
        form.addWidget(self.name)
        form.addWidget(self.allows_fraction)
        form.addWidget(self.add_button)
        layout.addLayout(form)

        self.message = make_message()
        layout.addWidget(self.message)
        self.table = make_table(["الكود", "الاسم", "تسمح بكسور"])
        layout.addWidget(self.table, 1)
        self.refresh()

    def add_unit(self) -> bool:
        try:
            self.master.create_unit(self.code.text(), self.name.text(),
                                    self.allows_fraction.isChecked())
        except Exception as exc:  # sqlite3 errors carry the real reason
            set_message(self.message, f"تعذر إضافة الوحدة: {exc}", error=True)
            return False
        self.code.clear()
        self.name.clear()
        set_message(self.message, "أُضيفت الوحدة.")
        self.refresh()
        return True

    def refresh(self) -> None:
        fill_table(self.table, [
            (code, name, "نعم" if fraction else "لا")
            for code, name, fraction in query_rows(
                self.context, "SELECT code,name_ar,allows_fraction FROM units ORDER BY code"
            )
        ])


class ProductsTab(QWidget):
    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        self.master = MasterDataService(context.db)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        form = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("كود الصنف")
        self.name = QLineEdit()
        self.name.setPlaceholderText("اسم الصنف")
        self.unit = QComboBox()
        self.tracking = QComboBox()
        for code, label in TRACKING_MODES:
            self.tracking.addItem(label, code)
        self.add_button = QPushButton("إضافة صنف")
        self.add_button.clicked.connect(self.add_product)
        form.addWidget(QLabel("الكود"))
        form.addWidget(self.code)
        form.addWidget(QLabel("الاسم"))
        form.addWidget(self.name)
        form.addWidget(QLabel("الوحدة"))
        form.addWidget(self.unit)
        form.addWidget(QLabel("التتبع"))
        form.addWidget(self.tracking)
        form.addWidget(self.add_button)
        layout.addLayout(form)

        actions = QHBoxLayout()
        self.deactivate_button = QPushButton("تعطيل الصنف المحدد")
        self.deactivate_button.setObjectName("Secondary")
        self.deactivate_button.clicked.connect(self.deactivate_selected)
        actions.addWidget(self.deactivate_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.message = make_message()
        layout.addWidget(self.message)
        self.table = make_table(["الكود", "الاسم", "الوحدة", "التتبع", "الحالة"])
        layout.addWidget(self.table, 1)
        self.refresh()

    def add_product(self) -> bool:
        unit_code = self.unit.currentData()
        if unit_code is None:
            set_message(self.message, "أضف وحدة أولاً قبل تسجيل صنف.", error=True)
            return False
        try:
            self.master.create_product(self.code.text(), self.name.text(),
                                       unit_code, self.tracking.currentData())
        except Exception as exc:
            set_message(self.message, f"تعذر إضافة الصنف: {exc}", error=True)
            return False
        self.code.clear()
        self.name.clear()
        set_message(self.message, "أُضيف الصنف.")
        self.refresh()
        return True

    def deactivate_product(self, product_id: int) -> None:
        self.master.deactivate_product(int(product_id))
        set_message(self.message, "عُطّل الصنف. الحركات التاريخية تبقى كما هي.")
        self.refresh()

    def deactivate_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            selected = self.table.selectionModel().selectedRows()
            row = selected[0].row() if selected else -1
        if row < 0:
            set_message(self.message, "اختر صنفًا من الجدول أولاً.", error=True)
            return
        item = self.table.item(row, 0)
        product_id = item.data(Qt.UserRole) if item is not None else None
        if product_id is None:
            set_message(self.message, "تعذر تحديد الصنف المحدد.", error=True)
            return
        self.deactivate_product(int(product_id))

    def refresh(self) -> None:
        current = self.unit.currentData()
        self.unit.clear()
        for code, name in query_rows(
            self.context,
            "SELECT u.code,u.name_ar FROM units u WHERE u.is_active=1 ORDER BY u.code",
        ):
            self.unit.addItem(f"{name} ({code})", code)
        if current is not None:
            index = self.unit.findData(current)
            if index >= 0:
                self.unit.setCurrentIndex(index)
        rows = query_rows(
            self.context,
            "SELECT p.id,p.code,p.name_ar,u.name_ar,p.tracking_mode,p.is_active "
            "FROM products p JOIN units u ON u.id=p.base_unit_id ORDER BY p.code",
        )
        fill_table(self.table, [
            (code, name, unit, "بكرة" if tracking == "REEL" else "دفعة",
             "نشط" if active else "معطّل")
            for _id, code, name, unit, tracking, active in rows
        ])
        for row_index, row in enumerate(rows):
            item = self.table.item(row_index, 0)
            if item is not None:
                item.setData(Qt.UserRole, row[0])


class CustomersTab(QWidget):
    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        self.master = MasterDataService(context.db)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        form = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("كود العميل")
        self.name = QLineEdit()
        self.name.setPlaceholderText("اسم العميل")
        self.customer_type = QComboBox()
        for code, label in CUSTOMER_TYPES:
            self.customer_type.addItem(label, code)
        self.add_button = QPushButton("إضافة عميل")
        self.add_button.clicked.connect(self.add_customer)
        form.addWidget(QLabel("الكود"))
        form.addWidget(self.code)
        form.addWidget(QLabel("الاسم"))
        form.addWidget(self.name)
        form.addWidget(QLabel("النوع"))
        form.addWidget(self.customer_type)
        form.addWidget(self.add_button)
        layout.addLayout(form)
        self.message = make_message()
        layout.addWidget(self.message)
        self.table = make_table(["الكود", "الاسم", "النوع"])
        layout.addWidget(self.table, 1)
        self.refresh()

    def add_customer(self) -> bool:
        try:
            self.master.create_customer(self.code.text(), self.name.text(),
                                        self.customer_type.currentData())
        except Exception as exc:
            set_message(self.message, f"تعذر إضافة العميل: {exc}", error=True)
            return False
        self.code.clear()
        self.name.clear()
        set_message(self.message, "أُضيف العميل.")
        self.refresh()
        return True

    def refresh(self) -> None:
        labels = dict(CUSTOMER_TYPES)
        fill_table(self.table, [
            (code, name, labels.get(customer_type, customer_type))
            for code, name, customer_type in query_rows(
                self.context,
                "SELECT code,name_ar,customer_type FROM customers ORDER BY code",
            )
        ])


class SuppliersTab(QWidget):
    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        self.master = MasterDataService(context.db)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        form = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setPlaceholderText("كود المورد")
        self.name = QLineEdit()
        self.name.setPlaceholderText("اسم المورد")
        self.add_button = QPushButton("إضافة مورد")
        self.add_button.clicked.connect(self.add_supplier)
        form.addWidget(QLabel("الكود"))
        form.addWidget(self.code)
        form.addWidget(QLabel("الاسم"))
        form.addWidget(self.name)
        form.addWidget(self.add_button)
        layout.addLayout(form)
        self.message = make_message()
        layout.addWidget(self.message)
        self.table = make_table(["الكود", "الاسم"])
        layout.addWidget(self.table, 1)
        self.refresh()

    def add_supplier(self) -> bool:
        try:
            self.master.create_supplier(self.code.text(), self.name.text())
        except Exception as exc:
            set_message(self.message, f"تعذر إضافة المورد: {exc}", error=True)
            return False
        self.code.clear()
        self.name.clear()
        set_message(self.message, "أُضيف المورد.")
        self.refresh()
        return True

    def refresh(self) -> None:
        fill_table(self.table, query_rows(
            self.context, "SELECT code,name_ar FROM suppliers ORDER BY code"
        ))


class ProjectsTab(QWidget):
    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        self.master = MasterDataService(context.db)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        form = QHBoxLayout()
        self.contractor = QComboBox()
        self.code = QLineEdit()
        self.code.setPlaceholderText("كود المشروع")
        self.name = QLineEdit()
        self.name.setPlaceholderText("اسم المشروع")
        self.add_button = QPushButton("إضافة مشروع")
        self.add_button.clicked.connect(self.add_project)
        form.addWidget(QLabel("المقاول"))
        form.addWidget(self.contractor)
        form.addWidget(QLabel("الكود"))
        form.addWidget(self.code)
        form.addWidget(QLabel("الاسم"))
        form.addWidget(self.name)
        form.addWidget(self.add_button)
        layout.addLayout(form)
        self.message = make_message()
        layout.addWidget(self.message)
        self.table = make_table(["الكود", "الاسم", "المقاول"])
        layout.addWidget(self.table, 1)
        self.refresh()

    def add_project(self) -> bool:
        customer_id = self.contractor.currentData()
        if customer_id is None:
            set_message(self.message, "أضف عميلاً من نوع «مقاول» أولاً.", error=True)
            return False
        try:
            self.master.create_project(int(customer_id), self.code.text(), self.name.text())
        except Exception as exc:
            set_message(self.message, f"تعذر إضافة المشروع: {exc}", error=True)
            return False
        self.code.clear()
        self.name.clear()
        set_message(self.message, "أُضيف المشروع.")
        self.refresh()
        return True

    def refresh(self) -> None:
        current = self.contractor.currentData()
        self.contractor.clear()
        for customer_id, label in query_rows(
            self.context,
            "SELECT id,code||' — '||name_ar FROM customers "
            "WHERE customer_type='CONTRACTOR' AND is_active=1 ORDER BY code",
        ):
            self.contractor.addItem(label, customer_id)
        if current is not None:
            index = self.contractor.findData(current)
            if index >= 0:
                self.contractor.setCurrentIndex(index)
        fill_table(self.table, query_rows(
            self.context,
            "SELECT pr.code,pr.name_ar,c.name_ar FROM projects pr "
            "JOIN customers c ON c.id=pr.customer_id ORDER BY pr.code",
        ))


class MasterDataPage(QWidget):
    """Tabbed master-data editor; tabs appear only with the matching permission."""

    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.units: UnitsTab | None = None
        self.products: ProductsTab | None = None
        self.customers: CustomersTab | None = None
        self.suppliers: SuppliersTab | None = None
        self.projects: ProjectsTab | None = None
        self.pricing: PricingTab | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(12)
        layout.addWidget(make_title("البيانات الأساسية"))
        layout.addWidget(make_subtitle(
            "الأصناف والوحدات والعملاء والموردون والمشاريع. لا يُحذف صنف أو عميل له حركات؛ "
            "يُستخدم التعطيل بدلاً من الحذف."
        ))

        card = make_card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        self.tabs = QTabWidget()
        if context.has_permission(user_id, "manage_products"):
            self.units = UnitsTab(context)
            self.products = ProductsTab(context)
            self.tabs.addTab(self.products, "الأصناف")
            self.tabs.addTab(self.units, "الوحدات")
        if context.has_permission(user_id, "manage_customers"):
            self.customers = CustomersTab(context)
            self.tabs.addTab(self.customers, "العملاء")
        if context.has_permission(user_id, "manage_suppliers"):
            self.suppliers = SuppliersTab(context)
            self.tabs.addTab(self.suppliers, "الموردون")
        if context.has_permission(user_id, "manage_projects"):
            self.projects = ProjectsTab(context)
            self.tabs.addTab(self.projects, "المشاريع")
        if context.has_permission(user_id, "manage_price_lists"):
            self.pricing = PricingTab(context, user_id)
            self.tabs.addTab(self.pricing, "أسعار البيع")
        if self.tabs.count() == 0:
            self.tabs.addTab(QWidget(), "لا توجد صلاحيات للبيانات الأساسية")
        card_layout.addWidget(self.tabs)
        layout.addWidget(card, 1)
        self.tabs.currentChanged.connect(self._refresh_current)

    def _refresh_current(self, index: int) -> None:
        widget = self.tabs.widget(index)
        refresh = getattr(widget, "refresh", None)
        if callable(refresh):
            refresh()

    def refresh(self) -> None:
        for tab in (
            self.units, self.products, self.customers, self.suppliers, self.projects, self.pricing,
        ):
            if tab is not None:
                tab.refresh()
