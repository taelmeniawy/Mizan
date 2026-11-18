"""Small reusable widgets for the desktop shell."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

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


def clear_layout(layout) -> None:
    """Remove every widget/child layout so a page can be rebuilt in place."""
    while layout.count():
        item = layout.takeAt(0)
        widget: QWidget | None = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())
