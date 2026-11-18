"""Small reusable widgets for the desktop shell."""

from __future__ import annotations

import re
from contextlib import closing

from PySide6.QtCore import QDate, QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFrame,
    QHeaderView,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..bootstrap import AppContext
from ..rendering import format_egp


def make_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Title")
    return label


def make_subtitle(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Subtitle")
    label.setWordWrap(True)
    return label


def make_hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


def make_message() -> QLabel:
    label = QLabel("")
    label.setObjectName("Message")
    label.setWordWrap(True)
    return label


def set_message(label: QLabel, text: str, *, error: bool = False) -> None:
    """Set an inline status message; errors are red, success stays green."""
    label.setText(text)
    label.setStyleSheet("color: #b42318;" if error else "color: #2f6f4f;")


def make_card() -> QFrame:
    card = QFrame()
    card.setObjectName("Card")
    card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    return card


def stat_card(caption: str) -> tuple[QFrame, QLabel]:
    card = make_card()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(6)
    value = QLabel("—")
    value.setObjectName("StatValue")
    value.setAlignment(Qt.AlignRight | Qt.AlignAbsolute)
    title = QLabel(caption)
    title.setObjectName("Subtitle")
    layout.addWidget(title)
    layout.addWidget(value)
    return card, value


def money(piastres: int) -> str:
    return format_egp(int(piastres))


def make_date_field() -> QDateEdit:
    field = QDateEdit(QDate.currentDate())
    field.setCalendarPopup(True)
    field.setLayoutDirection(Qt.LeftToRight)
    field.setDisplayFormat("dd/MM/yyyy")
    return field


def format_scaled(value: int, decimal_places: int = 6) -> str:
    whole, fraction = divmod(abs(value), 10**decimal_places)
    sign = "-" if value < 0 else ""
    return sign + f"{whole}.{fraction:0{decimal_places}d}".rstrip("0").rstrip(".")


def parse_scaled(text: str, label: str, decimal_places: int = 6) -> int:
    text = text.strip().replace("٫", ".")
    whole_digits = 19 - decimal_places
    if re.fullmatch(rf"\d{{1,{whole_digits}}}(?:\.\d{{1,{decimal_places}}})?", text) is None:
        raise ValueError(f"أدخل {label} كرقم حتى {decimal_places} خانات عشرية.")
    whole, _, fraction = text.partition(".")
    value = int(whole) * 10**decimal_places + int(fraction.ljust(decimal_places, "0"))
    if value > 2**63 - 1:
        raise ValueError(f"{label} أكبر من الحد المسموح.")
    return value


def populate_combo(combo: QComboBox, rows: list[tuple]) -> None:
    selected = combo.currentData()
    with QSignalBlocker(combo):
        combo.clear()
        for item_id, label in rows:
            combo.addItem(label, item_id)
        index = combo.findData(selected)
        if index >= 0:
            combo.setCurrentIndex(index)


def product_units(context: AppContext, product_id: int | None) -> list[tuple]:
    return query_rows(
        context,
        "SELECT u.id,u.code||' — '||u.name_ar FROM units u WHERE u.id IN ("
        "SELECT base_unit_id FROM products WHERE id=? UNION "
        "SELECT unit_id FROM product_unit_conversions WHERE product_id=?"
        ") ORDER BY u.code",
        (product_id, product_id),
    )


def make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return table


def fill_table(table: QTableWidget, rows: list[tuple]) -> None:
    table.setRowCount(len(rows))
    for row_index, values in enumerate(rows):
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row_index, column, item)


def amount_entries(table: QTableWidget, rows: list[tuple]) -> list[QLineEdit]:
    table.setRowCount(0)
    fill_table(table, rows)
    column = table.columnCount() - 1
    label = table.horizontalHeaderItem(column).text()
    fields = []
    for row, values in enumerate(rows):
        field = QLineEdit()
        field.setPlaceholderText("0")
        field.setLayoutDirection(Qt.LeftToRight)
        field.setAccessibleName(f"{label} {values[0]}")
        table.setCellWidget(row, column, field)
        table.setRowHeight(row, max(table.rowHeight(row), field.sizeHint().height()))
        fields.append(field)
    return fields


def query_rows(context: AppContext, sql: str, params: tuple = ()) -> list[tuple]:
    with closing(context.db.connect()) as conn:
        return [tuple(row) for row in conn.execute(sql, params)]


def clear_layout(layout) -> None:
    """Remove every widget/child layout so a page can be rebuilt in place."""
    while layout.count():
        item = layout.takeAt(0)
        widget: QWidget | None = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())
