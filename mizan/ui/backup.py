"""Backup, verification, off-device copy and restore screen.

Long operations run through :class:`mizan.background.BackgroundJob`; the page
polls the job state with a timer, so the window keeps responding.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..backup import BackupError
from ..background import BackgroundJob
from ..bootstrap import AppContext
from .common import (
    make_card,
    make_hint,
    make_message,
    make_subtitle,
    make_title,
    set_message,
)

STAGE_LABELS = {
    "database": "أخذ لقطة قاعدة البيانات",
    "documents": "نسخ المستندات المسجلة",
    "encrypt": "تشفير النسخة",
    "verify": "التحقق من البصمات والمخطط",
    "publish": "نشر النسخة",
    "source": "التحقق من الحزمة المصدر",
    "copy": "نسخ الحزمة",
}


class BackupPage(QWidget):
    def __init__(self, context: AppContext, user_id: int):
        super().__init__()
        self.context = context
        self.user_id = user_id
        self.job: BackgroundJob | None = None
        self.can_restore = context.has_permission(user_id, "restore_database")
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._poll)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(12)
        layout.addWidget(make_title("النسخ الاحتياطي والاستعادة"))
        layout.addWidget(make_subtitle(
            "نسخة متحققة من قاعدة البيانات والمستندات، تشفير اختياري بمفتاح منفصل، "
            "نسخة يومية وشهرية مع احتفاظ تلقائي، ونسخة خارج الجهاز قابلة للاستعادة."
        ))

        summary = make_card()
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(18, 14, 18, 14)
        summary_layout.setSpacing(5)
        self.daily_label = QLabel("—")
        self.monthly_label = QLabel("—")
        self.paths_label = make_hint("")
        summary_layout.addWidget(self.daily_label)
        summary_layout.addWidget(self.monthly_label)
        summary_layout.addWidget(self.paths_label)
        layout.addWidget(summary)

        key_card = make_card()
        key_layout = QHBoxLayout(key_card)
        key_layout.setContentsMargins(18, 12, 18, 12)
        key_layout.setSpacing(10)
        self.encrypt = QCheckBox("تشفير النسخة")
        self.key_path = QLineEdit()
        self.key_path.setReadOnly(True)
        self.key_path.setPlaceholderText("ملف مفتاح الاستعادة (32 بايت) — اختياري")
        key_browse = QPushButton("اختيار المفتاح")
        key_browse.setObjectName("Secondary")
        key_browse.clicked.connect(self._pick_key)
        key_layout.addWidget(self.encrypt)
        key_layout.addWidget(self.key_path, 1)
        key_layout.addWidget(key_browse)
        layout.addWidget(key_card)

        actions = make_card()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(18, 12, 18, 12)
        actions_layout.setSpacing(8)
        self.create_button = QPushButton("نسخة الآن")
        self.create_button.clicked.connect(self.create_backup)
        self.verify_button = QPushButton("تحقق من آخر نسخة")
        self.verify_button.setObjectName("Secondary")
        self.verify_button.clicked.connect(self.verify_latest)
        self.copy_button = QPushButton("نسخة خارج الجهاز")
        self.copy_button.setObjectName("Secondary")
        self.copy_button.clicked.connect(self._pick_offsite)
        self.folder_button = QPushButton("فتح مجلد النسخ")
        self.folder_button.setObjectName("Secondary")
        self.folder_button.clicked.connect(self._open_backups_folder)
        self.restore_dir_button = QPushButton("استعادة من مجلد")
        self.restore_dir_button.setObjectName("Danger")
        self.restore_dir_button.clicked.connect(self._pick_restore_directory)
        self.restore_file_button = QPushButton("استعادة من ملف مشفّر")
        self.restore_file_button.setObjectName("Danger")
        self.restore_file_button.clicked.connect(self._pick_restore_file)
        for button in (self.create_button, self.verify_button, self.copy_button,
                       self.folder_button, self.restore_dir_button, self.restore_file_button):
            actions_layout.addWidget(button)
        actions_layout.addStretch(1)
        layout.addWidget(actions)

        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.cancel_button = QPushButton("إلغاء")
        self.cancel_button.setObjectName("Secondary")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self.cancel)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.cancel_button)
        layout.addLayout(progress_row)

        self.message = make_message()
        layout.addWidget(self.message)
        layout.addWidget(make_hint(
            "الاستعادة لا تستبدل قاعدة البيانات المفتوحة؛ تُنشئ مجلدًا مستقلًا متحققًا "
            "يمكن فتحه لاحقًا. النسخ الأقدم من إصدار التطبيق غير مقبولة (سياسة الترقية في المرحلة 10)."
        ))
        layout.addStretch(1)

        if not self.can_restore:
            for button in (self.restore_dir_button, self.restore_file_button):
                button.setEnabled(False)
                button.setToolTip("تحتاج صلاحية استعادة نسخة.")
        self.refresh_summary()

    # ------------------------------------------------------------------
    # Summary / files
    # ------------------------------------------------------------------
    def refresh_summary(self) -> None:
        daily = self.context.schedule.bundles("daily")
        monthly = self.context.schedule.bundles("monthly")
        self.daily_label.setText(
            f"النسخ اليومية: {len(daily)} • آخرها: {daily[-1].name if daily else '—'}"
        )
        self.monthly_label.setText(
            f"النسخ الشهرية: {len(monthly)} • آخرها: {monthly[-1].name if monthly else '—'}"
        )
        self.paths_label.setText(f"مجلد النسخ: {self.context.paths.backups_root}")

    def _pick_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "اختر ملف مفتاح الاستعادة", "", "Recovery key (*.key *.*)"
        )
        if path:
            self.key_path.setText(path)
            self.encrypt.setChecked(True)

    def _open_backups_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.context.paths.backups_root)))

    def selected_key(self) -> bytes | None:
        if not self.encrypt.isChecked():
            return None
        text = self.key_path.text().strip()
        if not text:
            raise BackupError("اختر ملف مفتاح الاستعادة أولاً، أو ألغِ خيار التشفير.")
        key = Path(text).read_bytes()
        if len(key) != 32:
            raise BackupError("ملف المفتاح يجب أن يحتوي 32 بايت بالضبط.")
        return key

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def create_backup(self):
        try:
            key = self.selected_key()
        except (BackupError, OSError) as exc:
            set_message(self.message, str(exc), error=True)
            return None
        work = lambda progress, cancel: self.context.schedule.run(  # noqa: E731
            actor_id=self.user_id, key=key, progress=progress
        )
        return self._start(work, "جارٍ إنشاء النسخة اليومية والشهرية...")

    def verify_latest(self):
        bundles = self.context.schedule.bundles("daily")
        if not bundles:
            set_message(self.message, "لا توجد نسخة يومية للتحقق منها بعد.", error=True)
            return None
        bundle = bundles[-1]
        try:
            key = self.selected_key() if bundle.is_file() else None
        except (BackupError, OSError) as exc:
            set_message(self.message, str(exc), error=True)
            return None
        work = lambda progress, cancel: self.context.backups.verify(bundle, key=key)  # noqa: E731
        return self._start(work, f"جارٍ التحقق من النسخة: {bundle.name}")

    def copy_latest_offsite(self, destination: Path, key: bytes | None = None):
        bundles = self.context.schedule.bundles("daily")
        if not bundles:
            set_message(self.message, "لا توجد نسخة يومية لنسخها خارج الجهاز.", error=True)
            return None
        bundle = bundles[-1]
        work = lambda progress, cancel: self.context.backups.copy_to(  # noqa: E731
            bundle, destination, actor_id=self.user_id, key=key, progress=progress
        )
        return self._start(work, f"جارٍ نسخ الحزمة إلى: {destination}")

    def restore_bundle(self, bundle: Path, destination: Path, reason: str):
        if not self.can_restore:
            set_message(self.message, "تحتاج صلاحية استعادة نسخة.", error=True)
            return None
        if not reason.strip():
            set_message(self.message, "سبب الاستعادة مطلوب لتسجيله في سجل التدقيق.", error=True)
            return None
        try:
            key = self.selected_key() if Path(bundle).is_file() else None
        except (BackupError, OSError) as exc:
            set_message(self.message, str(exc), error=True)
            return None
        work = lambda progress, cancel: self.context.backups.restore(  # noqa: E731
            bundle, destination, actor_id=self.user_id, reason=reason.strip(),
            key=key, progress=progress,
        )
        return self._start(work, f"جارٍ الاستعادة إلى: {destination}")

    def cancel(self) -> None:
        if self.job is not None and self.job.is_running:
            self.job.cancel()
            set_message(self.message, "جارٍ الإلغاء قبل نشر النسخة...")

    # ------------------------------------------------------------------
    # Dialog-driven actions
    # ------------------------------------------------------------------
    def _stamp(self) -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S")

    def _pick_offsite(self) -> None:
        parent = QFileDialog.getExistingDirectory(self, "اختر مجلد النسخة الخارجية")
        if not parent:
            return
        try:
            key = self.selected_key()
        except (BackupError, OSError) as exc:
            set_message(self.message, str(exc), error=True)
            return
        suffix = ".mizan" if key is not None else ""
        self.copy_latest_offsite(Path(parent) / f"mizan-{self._stamp()}{suffix}", key=key)

    def _pick_restore_directory(self) -> None:
        bundle = QFileDialog.getExistingDirectory(self, "اختر مجلد النسخة")
        if bundle:
            self._restore_flow(Path(bundle))

    def _pick_restore_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "اختر ملف النسخة المشفّرة", "", "Mizan backup (*.mizan)"
        )
        if path:
            self._restore_flow(Path(path))

    def _restore_flow(self, bundle: Path) -> None:
        parent = QFileDialog.getExistingDirectory(self, "اختر مجلد الاستعادة")
        if not parent:
            return
        destination = Path(parent) / f"restored-{self._stamp()}"
        reason, accepted = QInputDialog.getText(
            self, "سبب الاستعادة", "سبب الاستعادة (يُسجَّل في سجل التدقيق):"
        )
        if not accepted or not reason.strip():
            set_message(self.message, "لم تُنفَّذ الاستعادة: السبب مطلوب.", error=True)
            return
        self.restore_bundle(bundle, destination, reason)

    # ------------------------------------------------------------------
    # Background job plumbing
    # ------------------------------------------------------------------
    def _start(self, work, description: str):
        if self.job is not None and self.job.is_running:
            set_message(self.message, "عملية نسخ/استعادة قيد التنفيذ بالفعل.", error=True)
            return None
        self.job = BackgroundJob(work, name="mizan-backup-ui").start()
        self._set_busy(True)
        set_message(self.message, description)
        self._timer.start()
        return self.job

    def _set_busy(self, busy: bool) -> None:
        for button in (self.create_button, self.verify_button, self.copy_button,
                       self.restore_dir_button, self.restore_file_button):
            button.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)

    def _poll(self) -> None:
        if self.job is None:
            return
        state = self.job.state
        if state.status in ("pending", "running"):
            set_message(self.message, f"{STAGE_LABELS.get(state.stage, 'جارٍ التنفيذ')}…")
            return
        self._timer.stop()
        self._set_busy(False)
        if state.status == "succeeded":
            message = "تمت العملية بنجاح."
            if isinstance(state.result, Path):
                message += f" المسار: {state.result}"
            elif isinstance(state.result, dict) and "created" in state.result:
                message += f" النسخ المنشأة: {len(state.result['created'])}."
            set_message(self.message, message)
            self.refresh_summary()
        elif state.status == "cancelled":
            set_message(self.message, "أُلغيت العملية قبل النشر، ولم تبق أي نسخة جزئية.",
                        error=True)
        else:
            set_message(self.message, f"فشلت العملية: {state.error}", error=True)
