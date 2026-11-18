"""Startup integrity report (database, schema and managed documents)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..bootstrap import AppContext
from ..diagnostics import startup_check
from .common import make_card, make_hint, make_message, make_title, set_message

_SCHEMA_STATES = {
    "current": "مطابق للإصدار الحالي",
    "outdated": "أقدم من التطبيق — يلزم ترحيل",
    "newer": "أحدث من التطبيق",
    "unknown": "غير معروف",
}


class DiagnosticsPage(QWidget):
    def __init__(self, context: AppContext):
        super().__init__()
        self.context = context
        self.report: dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(make_title("فحص سلامة البيانات"))
        header.addStretch(1)
        self.refresh_button = QPushButton("إعادة الفحص")
        self.refresh_button.setObjectName("Secondary")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)
        layout.addWidget(make_hint(
            "يُشغَّل هذا الفحص عند بدء التطبيق. لا يُصلح شيئًا ولا يعيد توليد أي مستند؛ "
            "المستند المفقود أو المتغيّر يُبلَّغ فقط ويُعالَج بالاستعادة من نسخة متحققة."
        ))

        status = make_card()
        form = QFormLayout(status)
        form.setContentsMargins(18, 14, 18, 14)
        form.setSpacing(7)
        self.overall = QLabel("—")
        self.database = QLabel("—")
        self.documents = QLabel("—")
        self.schema = QLabel("—")
        for label in (self.overall, self.database, self.documents, self.schema):
            label.setWordWrap(True)
        form.addRow("الحالة العامة", self.overall)
        form.addRow("قاعدة البيانات", self.database)
        form.addRow("المستندات", self.documents)
        form.addRow("المخطط", self.schema)
        layout.addWidget(status)

        self.message = make_message()
        layout.addWidget(self.message)

        layout.addWidget(make_hint("التفاصيل والمشكلات:"))
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(180)
        layout.addWidget(self.details, 1)
        self.refresh()

    def refresh(self) -> None:
        report = startup_check(self.context.db, self.context.paths.documents_root)
        self.report = report
        database = report["database"]
        documents = report["documents"]
        ok = report["ok"]

        self.overall.setText("سليم" if ok else "توجد مشكلات تحتاج معالجة")
        self.overall.setStyleSheet(
            "color: #2f6f4f; font-weight: bold;" if ok else "color: #b42318; font-weight: bold;"
        )
        integrity = database["integrity"]
        self.database.setText(
            f"سلامة: {integrity if integrity == 'ok' else integrity} • "
            f"المفاتيح الخارجية: {'سليمة' if database['foreign_keys_ok'] else 'غير سليمة'} • "
            f"الملف: {database['path']}"
        )
        missing = len(documents["missing"])
        mismatched = len(documents["mismatched"])
        unknown = len(documents["unreadable"])
        temporary = documents.get("temporary_files", 0)
        self.documents.setText(
            f"تم فحص {documents['checked']} مستندًا • مفقود: {missing} • "
            f"غير مطابق: {mismatched} • غير قابل للقراءة: {unknown} • "
            f"ملفات مؤقتة متروكة: {temporary}"
        )
        self.schema.setText(
            f"{_SCHEMA_STATES.get(database['schema_state'], database['schema_state'])}"
            + (f" • {database['error']}" if database["error"] else "")
        )

        lines = []
        lines.extend(f"خطأ: {error}" for error in report["errors"])
        lines.extend(f"تحذير: {warning}" for warning in report["warnings"])
        for item in documents["missing"]:
            lines.append(f"مستند مفقود: {item['relative_path']}")
        for item in documents["mismatched"]:
            lines.append(
                f"مستند غير مطابق ({item['reason']}): {item['relative_path']} "
                f"— المتوقع {item['expected_sha256']}"
            )
        for item in documents["unreadable"]:
            lines.append(f"مستند غير قابل للقراءة: {item['relative_path']} — {item['reason']}")
        if documents.get("skipped"):
            lines.append(f"ملاحظة: {documents['skipped']}")
        self.details.setPlainText("\n".join(lines) if lines else "لا توجد مشكلات مسجلة.")
        set_message(
            self.message,
            "الفحص مكتمل." if ok else "راجع التفاصيل أدناه قبل متابعة العمل.",
            error=not ok,
        )
