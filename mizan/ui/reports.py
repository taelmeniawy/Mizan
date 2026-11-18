"""Permission-aware reports with period filters and read-only result tables."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import QAbstractTableModel, QDate, QModelIndex, QSignalBlocker, QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
    QPushButton, QTabWidget, QTableView, QVBoxLayout, QWidget,
)

from ..background import BackgroundJob
from ..bootstrap import AppContext
from .common import make_date_field, make_hint, make_message, make_title, query_rows, set_message
from .report_catalog import FINANCE, REPORTS, Column, load_report

FILTER_LABELS = {
    "customer_id": ("العميل", "كل العملاء"),
    "supplier_id": ("المورد", "كل الموردين"),
    "product_id": ("الصنف", "كل الأصناف"),
    "posted_by": ("رحّلها", "كل المستخدمين"),
    "financial_account_id": ("الحساب المالي", "كل الحسابات"),
    "account_type": ("نوع الحساب", "كل الأنواع"),
}
FILTER_QUERIES = {
    "customer_id": "SELECT id,code||' — '||name_ar FROM customers ORDER BY code",
    "supplier_id": "SELECT id,code||' — '||name_ar FROM suppliers ORDER BY code",
    "product_id": "SELECT id,code||' — '||name_ar FROM products ORDER BY code",
    "posted_by": "SELECT id,display_name FROM users ORDER BY display_name,id",
    "financial_account_id": "SELECT id,code||' — '||name_ar FROM financial_accounts ORDER BY code",
}


class ReportRows(QAbstractTableModel):
    def __init__(self, columns: tuple[Column, ...], rows: list[dict], parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows = rows

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and 0 <= section < len(self.columns):
            return self.columns[section].title
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.TextAlignmentRole:
            return Qt.AlignRight | Qt.AlignVCenter
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            column = self.columns[index.column()]
            return column.display(self.rows[index.row()].get(column.key))
        return None


class ReportsPage(QWidget):
    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.permissions = context.permissions.permissions(user_id)
        self.job: BackgroundJob | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self._poll)
        self.filters: dict[str, QComboBox] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.addWidget(make_title("التقارير"))

        self.controls = QWidget()
        selections = QVBoxLayout(self.controls)
        selections.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.report = QComboBox()
        label = QLabel("التقرير")
        label.setBuddy(self.report)
        self.run_button = QPushButton("عرض التقرير")
        self.run_button.clicked.connect(self.run)
        top.addWidget(label)
        top.addWidget(self.report, 1)
        top.addWidget(self.run_button)
        selections.addLayout(top)

        dates = QHBoxLayout()
        self.from_label = QLabel("من")
        self.through_label = QLabel("إلى")
        self.from_date = make_date_field()
        today = QDate.currentDate()
        self.from_date.setDate(QDate(today.year(), today.month(), 1))
        self.through_date = make_date_field()
        self.from_label.setBuddy(self.from_date)
        self.through_label.setBuddy(self.through_date)
        for field in (self.from_label, self.from_date, self.through_label, self.through_date):
            dates.addWidget(field)
        dates.addStretch(1)
        selections.addLayout(dates)

        self.filter_bar = QWidget()
        self.filter_layout = QGridLayout(self.filter_bar)
        self.filter_layout.setContentsMargins(0, 0, 0, 0)
        selections.addWidget(self.filter_bar)
        layout.addWidget(self.controls)
        self.scope = make_hint("")
        layout.addWidget(self.scope)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.message = make_message()
        self.message.setTextFormat(Qt.PlainText)
        layout.addWidget(self.message)
        self.totals = QLabel()
        self.totals.setWordWrap(True)
        self.totals.setTextFormat(Qt.PlainText)
        self.totals.setObjectName("Subtitle")
        layout.addWidget(self.totals)
        self.results = QTabWidget()
        layout.addWidget(self.results, 1)
        self.report.currentIndexChanged.connect(self._configure)
        self.from_date.dateChanged.connect(self._clear_results)
        self.through_date.dateChanged.connect(self._clear_results)
        self._report_options()

    def _report_options(self) -> None:
        selected = self.report.currentData()
        with QSignalBlocker(self.report):
            self.report.clear()
            for key, spec in REPORTS.items():
                if self.permissions.intersection(spec.permissions):
                    self.report.addItem(spec.title, key)
            self.report.setCurrentIndex(max(0, self.report.findData(selected)))
        self._configure()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        try:
            permissions = self.context.permissions.permissions(self.user_id)
        except sqlite3.Error as exc:
            self._clear_results()
            self.run_button.setEnabled(False)
            set_message(self.message, f"تعذر التحقق من صلاحيات التقارير. {exc}", error=True)
            return
        if self.permissions != permissions:
            self.permissions = permissions
            self._report_options()
        elif self.job is None:
            self._configure()

    def _clear_results(self) -> None:
        while self.results.count():
            table = self.results.widget(0)
            self.results.removeTab(0)
            table.deleteLater()
        self.totals.clear()
        self.message.clear()

    def _configure(self) -> None:
        selected = {name: field.currentData() for name, field in self.filters.items()}
        self._clear_results()
        while self.filter_layout.count():
            self.filter_layout.takeAt(0).widget().deleteLater()
        for column in range(self.filter_layout.columnCount()):
            self.filter_layout.setColumnStretch(column, 0)
        self.filters.clear()
        key = self.report.currentData()
        self.run_button.setEnabled(key is not None)
        if key is None:
            set_message(self.message, "الحساب لا يملك صلاحية عرض التقارير.", error=True)
            return
        spec = REPORTS[key]
        for field in (self.from_label, self.from_date):
            field.setVisible(spec.dates == "range")
        for field in (self.through_label, self.through_date):
            field.setVisible(spec.dates != "current")
        self.through_label.setText("بتاريخ" if spec.dates == "as_of" else "إلى")
        restricted = key == "sales" and FINANCE not in self.permissions
        scope = ""
        if restricted:
            scope = "يعرض التقرير حركات المبيعات التي رحّلتها."
        elif key == "cash":
            scope = "تُجمع حركة الحسابات المالية المرتبطة بنفس حساب الأستاذ."
        elif spec.dates == "current":
            scope = "الأرصدة الحالية، بما في ذلك الدفعات والبكرات التاريخية."
        self.scope.setText(scope)
        self.scope.setVisible(bool(self.scope.text()))
        try:
            for name in spec.filters:
                if name == "posted_by" and restricted:
                    continue
                self._add_filter(name, selected.get(name), required=name == spec.required_filter)
        except sqlite3.Error as exc:
            self.run_button.setEnabled(False)
            set_message(self.message, f"تعذر تحميل مرشحات التقرير. {exc}", error=True)
        self.filter_bar.setVisible(bool(self.filters))

    def _add_filter(self, name: str, selected, *, required: bool) -> None:
        caption, default = FILTER_LABELS[name]
        field = QComboBox()
        field.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        field.setMinimumContentsLength(9)
        field.addItem(f"اختر {caption}" if required else default, None)
        if name == "account_type":
            rows = [("REVENUE", "الإيرادات"), ("EXPENSE", "المصروفات")]
        else:
            rows = query_rows(self.context, FILTER_QUERIES[name])
        for value, title in rows:
            field.addItem(title, value)
        field.setCurrentIndex(max(0, field.findData(selected)))
        field.currentIndexChanged.connect(self._clear_results)
        label = QLabel(caption)
        label.setBuddy(field)
        column = len(self.filters) * 2
        self.filter_layout.addWidget(label, 0, column)
        self.filter_layout.addWidget(field, 0, column + 1)
        self.filter_layout.setColumnStretch(column + 1, 1)
        self.filters[name] = field

    def run(self) -> BackgroundJob | None:
        key = self.report.currentData()
        if key is None or self.job is not None or not self.run_button.isEnabled():
            return None
        self._clear_results()
        context, user = self.context, self.user_id
        start_date = self.from_date.date().toString(Qt.ISODate)
        end_date = self.through_date.date().toString(Qt.ISODate)
        filters = {name: field.currentData() for name, field in self.filters.items()}
        spec = REPORTS[key]
        if spec.dates == "range" and start_date > end_date:
            set_message(self.message, "تاريخ البداية لازم يسبق تاريخ النهاية أو يساويه.", error=True)
            return None
        if spec.required_filter and filters.get(spec.required_filter) is None:
            set_message(self.message, "اختر طرف الحساب لعرض الكشف.", error=True)
            return None

        def work(progress, cancelled):
            progress("read")
            result, permissions = load_report(context, user, key, start_date, end_date, filters)
            return key, result, permissions

        self.job = BackgroundJob(work, "mizan-report").start()
        self.controls.setEnabled(False)
        self.progress.show()
        self.timer.start()
        return self.job

    def _poll(self) -> None:
        if self.job is None:
            return
        state = self.job.state
        if state.status in ("pending", "running"):
            return
        self.job = None
        self.timer.stop()
        self.controls.setEnabled(True)
        self.progress.hide()
        if state.status != "succeeded":
            set_message(self.message, f"تعذر عرض التقرير. {state.error}", error=True)
            return
        key, result, permissions = state.result
        if key != self.report.currentData():
            return
        try:
            if self.context.permissions.permissions(self.user_id) != permissions:
                set_message(self.message, "تغيّرت الصلاحيات أثناء التحميل. أعد عرض التقرير.", error=True)
                return
        except sqlite3.Error as exc:
            set_message(self.message, f"تعذر التحقق من صلاحية التقرير. {exc}", error=True)
            return
        self._display(result, permissions)

    def _display(self, result: dict, permissions: set[str]) -> None:
        spec = REPORTS[self.report.currentData()]
        totals = [
            f"{column.title} {column.display(result[column.key])}"
            for column in spec.totals if column.permission is None or column.permission in permissions
        ]
        self.totals.setText("    •    ".join(totals))
        has_rows = False
        for section in spec.tables:
            columns = tuple(column for column in section.columns
                            if column.permission is None or column.permission in permissions)
            rows = result[section.source]
            has_rows |= bool(rows)
            table = QTableView(self.results)
            table.setModel(ReportRows(columns, rows, table))
            table.setAlternatingRowColors(True)
            table.verticalHeader().setVisible(False)
            table.setSelectionBehavior(QTableView.SelectRows)
            header = table.horizontalHeader()
            header.setMaximumSectionSize(290)
            header.setResizeContentsPrecision(30)
            header.setSectionResizeMode(QHeaderView.Stretch if len(columns) <= 6 else QHeaderView.ResizeToContents)
            header.setStretchLastSection(True)
            self.results.addTab(table, section.title)
        if not has_rows:
            self.message.setText("لا توجد بيانات مطابقة للمرشحات المحددة.")
