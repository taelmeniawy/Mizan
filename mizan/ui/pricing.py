"""Desktop editor for default, price-list and customer sale prices."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..bootstrap import AppContext
from ..master import MasterDataService
from .common import (
    fill_table, format_scaled, make_message, make_table, parse_scaled, populate_combo,
    product_units, query_rows, set_message,
)


class PricingTab(QWidget):
    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.master = MasterDataService(context.db)
        self.saved_prices: list[tuple] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        self.scope = QComboBox()
        self.scope.addItem("السعر الافتراضي للصنف", "product_default_prices")
        self.scope.addItem("سعر في قائمة", "price_list_items")
        self.scope.addItem("سعر خاص بعميل", "customer_prices")
        self.target_label = QLabel()
        self.target = QComboBox()
        self.product = QComboBox()
        self.unit = QComboBox()
        self.price = QLineEdit()
        self.price.setLayoutDirection(Qt.LeftToRight)
        self.price.setMaxLength(21)
        self.price.setPlaceholderText("0.00")
        self.save_button = QPushButton("حفظ السعر")
        editor = QGridLayout()
        for row, column, text, field in (
            (0, 0, "نوع السعر", self.scope),
            (1, 0, "الصنف", self.product),
            (1, 2, "الوحدة", self.unit),
            (2, 0, "السعر بالجنيه المصري", self.price),
        ):
            label = QLabel(text)
            label.setBuddy(field)
            editor.addWidget(label, row, column)
            editor.addWidget(field, row, column + 1)
        self.target_label.setBuddy(self.target)
        editor.addWidget(self.target_label, 0, 2)
        editor.addWidget(self.target, 0, 3)
        editor.addWidget(self.save_button, 2, 2, 1, 2, Qt.AlignRight)
        editor.setColumnStretch(1, 2)
        editor.setColumnStretch(3, 1)
        layout.addLayout(editor)

        self.new_list = QGroupBox("قائمة أسعار جديدة")
        new_list_layout = QHBoxLayout(self.new_list)
        self.list_code = QLineEdit()
        self.list_name = QLineEdit()
        self.list_code.setPlaceholderText("كود القائمة")
        self.list_code.setAccessibleName("كود القائمة")
        self.list_name.setPlaceholderText("اسم القائمة")
        self.list_name.setAccessibleName("اسم القائمة")
        self.list_type = QComboBox()
        self.list_type.setAccessibleName("نوع القائمة")
        for code, label in (
            ("GENERAL", "عامة"),
            ("RETAIL", "تجزئة"),
            ("WHOLESALE", "جملة"),
            ("CONTRACTOR", "مقاولون"),
            ("CUSTOM", "مخصصة"),
        ):
            self.list_type.addItem(label, code)
        self.add_list_button = QPushButton("إضافة القائمة")
        new_list_layout.addWidget(self.list_code)
        new_list_layout.addWidget(self.list_name)
        new_list_layout.addWidget(self.list_type)
        new_list_layout.addWidget(self.add_list_button)
        layout.addWidget(self.new_list)

        self.message = make_message()
        layout.addWidget(self.message)
        self.table = make_table(["الصنف", "الوحدة", "السعر بالجنيه المصري"])
        layout.addWidget(self.table, 1)

        self.scope.currentIndexChanged.connect(self._refresh_scope)
        self.target.currentIndexChanged.connect(self._refresh_prices)
        self.product.currentIndexChanged.connect(self._refresh_units)
        self.unit.currentIndexChanged.connect(self._show_price)
        self.table.cellClicked.connect(self._select_price)
        self.save_button.clicked.connect(self.save_price)
        self.add_list_button.clicked.connect(self.add_price_list)
        self.refresh()

    def refresh(self) -> None:
        populate_combo(self.product, query_rows(
            self.context,
            "SELECT id,code||' — '||name_ar FROM products WHERE is_active=1 ORDER BY code",
        ))
        self._refresh_scope()
        self._refresh_units()

    def _refresh_scope(self) -> None:
        table = self.scope.currentData()
        is_list = table == "price_list_items"
        has_target = table != "product_default_prices"
        self.target_label.setText("قائمة الأسعار" if is_list else "العميل")
        self.target_label.setVisible(has_target)
        self.target.setVisible(has_target)
        self.new_list.setVisible(is_list)
        # Customer IDs and list IDs can overlap. Reset the selection on scope changes.
        if self.target.property("scope") != table:
            with QSignalBlocker(self.target):
                self.target.clear()
            self.target.setProperty("scope", table)
            self.message.clear()
        if has_target:
            source = "price_lists" if is_list else "customers"
            populate_combo(self.target, query_rows(
                self.context,
                f"SELECT id,code||' — '||name_ar FROM {source} WHERE is_active=1 ORDER BY code",
            ))
        self._refresh_prices()

    def _refresh_units(self) -> None:
        populate_combo(self.unit, product_units(self.context, self.product.currentData()))
        self._show_price()

    def _refresh_prices(self) -> None:
        table = self.scope.currentData()
        where, params = "", ()
        if table == "price_list_items":
            where, params = "WHERE price.price_list_id=?", (self.target.currentData(),)
        elif table == "customer_prices":
            where, params = "WHERE price.customer_id=?", (self.target.currentData(),)
        self.saved_prices = query_rows(
            self.context,
            "SELECT p.id,u.id,p.code||' — '||p.name_ar,u.name_ar,price.price_micros "
            f"FROM {table} price JOIN products p ON p.id=price.product_id "
            "JOIN units u ON u.id=price.unit_id "
            f"{where} ORDER BY p.code,u.code",
            params,
        )
        self.table.clearSelection()
        fill_table(self.table, [
            (product, unit, format_scaled(price))
            for _, _, product, unit, price in self.saved_prices
        ])
        self._show_price()

    def _show_price(self) -> None:
        product, unit = self.product.currentData(), self.unit.currentData()
        can_save = unit is not None and (
            self.scope.currentData() == "product_default_prices"
            or self.target.currentData() is not None
        )
        self.price.setEnabled(can_save)
        self.save_button.setEnabled(can_save)
        for product_id, unit_id, _, _, price in self.saved_prices:
            if (product_id, unit_id) == (product, unit):
                self.price.setText(format_scaled(price))
                return
        self.price.clear()

    def _select_price(self, row: int, _column: int) -> None:
        product_id, unit_id, *_ = self.saved_prices[row]
        product = self.product.findData(product_id)
        if product < 0:
            set_message(self.message, "الصنف معطّل.", error=True)
            return
        self.product.setCurrentIndex(product)
        unit = self.unit.findData(unit_id)
        if unit < 0:
            set_message(self.message, "الوحدة غير متاحة لهذا الصنف.", error=True)
            return
        self.unit.setCurrentIndex(unit)
        self._show_price()
        self.message.clear()

    def save_price(self) -> bool:
        table = self.scope.currentData()
        try:
            self.context.permissions.require(self.user_id, "manage_price_lists")
            if not self.save_button.isEnabled():
                raise ValueError("اختر الصنف والوحدة وبيانات السعر أولًا.")
            values = {
                "product_id": self.product.currentData(),
                "unit_id": self.unit.currentData(),
                "price_micros": parse_scaled(self.price.text(), "السعر بالجنيه المصري"),
            }
            if table == "price_list_items":
                values["price_list_id"] = self.target.currentData()
            elif table == "customer_prices":
                values["customer_id"] = self.target.currentData()
            self.master.set_price(table, update_existing=True, **values)
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية إدارة أسعار البيع.", error=True)
            return False
        except (ValueError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر حفظ السعر. {exc}", error=True)
            return False
        self._refresh_prices()
        set_message(self.message, "حُفظ السعر.")
        return True

    def add_price_list(self) -> bool:
        code, name = self.list_code.text().strip(), self.list_name.text().strip()
        try:
            self.context.permissions.require(self.user_id, "manage_price_lists")
            if not code or not name:
                raise ValueError("أدخل كود القائمة واسمها.")
            list_id = self.master.create_price_list(code, name, self.list_type.currentData())
        except PermissionError:
            set_message(self.message, "لا تملك صلاحية إدارة أسعار البيع.", error=True)
            return False
        except sqlite3.IntegrityError:
            set_message(self.message, "كود قائمة الأسعار مستخدم بالفعل.", error=True)
            return False
        except (ValueError, sqlite3.Error) as exc:
            set_message(self.message, f"تعذر إضافة القائمة. {exc}", error=True)
            return False
        self.list_code.clear()
        self.list_name.clear()
        self._refresh_scope()
        self.target.setCurrentIndex(self.target.findData(list_id))
        set_message(self.message, "أُضيفت قائمة الأسعار.")
        return True
