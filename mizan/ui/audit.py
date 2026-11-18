"""Audit-trail viewer (permission: view_audit_log)."""

from __future__ import annotations

from contextlib import closing

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..audit import AuditQueryError
from ..auth import PermissionDenied
from ..bootstrap import AppContext
from .common import make_card, make_hint, make_message, make_title, set_message


class AuditPage(QWidget):
    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.offset = 0
        self.page = {"events": [], "total": 0, "limit": 100, "offset": 0}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(12)
        layout.addWidget(make_title("سجل التدقيق"))
        layout.addWidget(make_hint(
            "السجل للإضافة فقط: لا يمكن تعديل الأحداث أو حذفها بعد تسجيلها."
        ))

        filters = make_card()
        filter_layout = QHBoxLayout(filters)
        filter_layout.setContentsMargins(16, 12, 16, 12)
        filter_layout.setSpacing(10)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.event_combo = QComboBox()
        self.from_date = QDateEdit(QDate.currentDate().addMonths(-1))
        self.to_date = QDateEdit(QDate.currentDate())
        for widget in (self.from_date, self.to_date):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
        self.limit = QSpinBox()
        self.limit.setRange(1, 500)
        self.limit.setValue(100)
        form.addRow("نوع الحدث", self.event_combo)
        form.addRow("من تاريخ", self.from_date)
        form.addRow("إلى تاريخ", self.to_date)
        form.addRow("حجم الصفحة", self.limit)
        filter_layout.addLayout(form)
        buttons = QVBoxLayout()
        self.refresh_button = QPushButton("بحث")
        self.refresh_button.clicked.connect(self._search)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        filter_layout.addLayout(buttons)
        layout.addWidget(filters)

        self.message = make_message()
        layout.addWidget(self.message)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["الوقت", "الحدث", "الكيان", "المستخدم", "السبب"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        navigation = QHBoxLayout()
        self.prev_button = QPushButton("السابق")
        self.prev_button.setObjectName("Secondary")
        self.prev_button.clicked.connect(self._previous)
        self.next_button = QPushButton("التالي")
        self.next_button.setObjectName("Secondary")
        self.next_button.clicked.connect(self._next)
        self.page_label = QLabel("")
        navigation.addWidget(self.prev_button)
        navigation.addWidget(self.next_button)
        navigation.addStretch(1)
        navigation.addWidget(self.page_label)
        layout.addLayout(navigation)

        self._load_event_types()
        self.reload()

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------
    def _load_event_types(self) -> None:
        self.event_combo.addItem("كل الأحداث", None)
        try:
            with closing(self.context.db.connect()) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT event_type FROM audit_events ORDER BY event_type"
                ).fetchall()
        except Exception:
            rows = []
        for row in rows:
            self.event_combo.addItem(row["event_type"], row["event_type"])

    def _search(self) -> None:
        self.reload(offset=0)

    def _previous(self) -> None:
        self.reload(offset=max(0, self.offset - self.limit.value()))

    def _next(self) -> None:
        self.reload(offset=self.offset + self.limit.value())

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def reload(self, offset: int | None = None) -> None:
        if offset is not None:
            self.offset = offset
        try:
            self.page = self.context.audit.query(
                self.user_id,
                event_type=self.event_combo.currentData(),
                from_date=self.from_date.date().toString("yyyy-MM-dd"),
                through_date=self.to_date.date().toString("yyyy-MM-dd"),
                limit=self.limit.value(),
                offset=self.offset,
            )
        except (PermissionDenied, AuditQueryError) as exc:
            self.table.setRowCount(0)
            set_message(self.message, f"تعذر تحميل سجل التدقيق: {exc}", error=True)
            return
        self._fill(self.page)
        set_message(self.message, "")

    def _fill(self, page: dict) -> None:
        events = page["events"]
        self.table.setRowCount(len(events))
        for row, event in enumerate(events):
            entity = event["entity_type"]
            if event["entity_id"] is not None:
                entity = f"{entity} #{event['entity_id']}"
            when = (event["occurred_at"] or "").replace("T", " ")[:16]
            values = (
                when,
                event["event_type"],
                entity,
                event.get("actor_username") or "—",
                event.get("reason") or "",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, column, item)
        self.offset = page["offset"]
        total = page["total"]
        start = page["offset"] + 1 if total else 0
        end = min(page["offset"] + len(events), total)
        self.page_label.setText(f"عرض {start}–{end} من {total}")
        self.prev_button.setEnabled(page["offset"] > 0)
        self.next_button.setEnabled(page["offset"] + page["limit"] < total)
