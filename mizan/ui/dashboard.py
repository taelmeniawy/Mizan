"""Read-only operational summary shown on the first screen."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..bootstrap import AppContext
from .common import make_message, make_hint, make_subtitle, make_title, money, set_message, stat_card


class DashboardPage(QWidget):
    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        self.values: dict[str, QLabel] = {}
        self.message = make_message()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(14)
        header = QHBoxLayout()
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(make_title("لوحة التشغيل"))
        texts.addWidget(make_subtitle("ملخص سريع للحركة التشغيلية والرصيد النقدي."))
        header.addLayout(texts)
        header.addStretch(1)
        self.refresh_button = QPushButton("تحديث")
        self.refresh_button.setObjectName("Secondary")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        cards = (
            ("products", "المنتجات النشطة"),
            ("customers", "العملاء النشطون"),
            ("sales", "فواتير بيع مرحّلة"),
            ("cash", "رصيد النقدية"),
            ("quotations", "عروض مفتوحة"),
            ("suppliers", "الموردون النشطون"),
        )
        for index, (key, caption) in enumerate(cards):
            card, value = stat_card(caption)
            self.values[key] = value
            grid.addWidget(card, index // 3, index % 3)
        layout.addLayout(grid)

        layout.addWidget(self.message)
        self.updated = make_hint("")
        layout.addWidget(self.updated)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        try:
            products = self._count("products", "is_active = 1")
            customers = self._count("customers", "is_active = 1")
            suppliers = self._count("suppliers", "is_active = 1")
            posted_sales = self._count("sales", "status = 'POSTED'")
            open_quotations = self._count("quotations", "status IN ('ISSUED','ACCEPTED')")
            self.values["products"].setText(f"{products:,}")
            self.values["customers"].setText(f"{customers:,}")
            self.values["suppliers"].setText(f"{suppliers:,}")
            self.values["sales"].setText(f"{posted_sales:,}")
            self.values["quotations"].setText(f"{open_quotations:,}")
            self.values["cash"].setText(money(self.context.accounting.account_balance("1010")))
            self.updated.setText(f"آخر تحديث: {datetime.now():%Y-%m-%d %H:%M}")
            set_message(self.message, "")
        except Exception as exc:  # A dashboard must never block the shell.
            set_message(self.message, f"تعذر تحديث الملخص: {exc}", error=True)

    def _count(self, table: str, condition: str) -> int:
        return int(self.context.db.scalar(f"SELECT COUNT(*) FROM {table} WHERE {condition}") or 0)
