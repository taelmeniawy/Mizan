"""Qt application setup: RTL layout, approved Arabic font and light theme."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from ..rendering import load_bundled_fonts

APP_NAME = "Mizan"
APP_DISPLAY_NAME = "ميزان"
APP_ORGANIZATION = "Mizan"

STYLESHEET = """
QWidget { background: #f3f5f8; color: #17212b; }
QMainWindow, QDialog { background: #f3f5f8; }
QFrame#Card { background: #ffffff; border: 1px solid #dde3ea; border-radius: 10px; }
QFrame#Sidebar { background: #10263f; border: none; }
QLabel#Brand { color: #ffffff; font-size: 24px; font-weight: bold; padding: 18px 16px 4px 16px; }
QLabel#BrandHint { color: #9fb3c8; padding: 0 16px 16px 16px; font-size: 11px; }
QLabel#Title { font-size: 21px; font-weight: bold; }
QLabel#Subtitle { color: #5b6b7c; }
QLabel#StatValue { font-size: 20px; font-weight: bold; color: #10263f; }
QLabel#Message { color: #2f6f4f; }
QLabel#SidebarUser { color: #d7e2ee; padding: 0 16px; }
QLabel#SidebarRole { color: #8fa6bd; padding: 0 16px 10px 16px; font-size: 11px; }
QLabel#Hint { color: #64748b; font-size: 11px; }
QListWidget#Nav { background: transparent; border: none; color: #cfdae7; font-size: 14px; outline: none; }
QListWidget#Nav::item { padding: 11px 14px; border-radius: 8px; margin: 2px 10px; }
QListWidget#Nav::item:selected { background: #1f4e79; color: #ffffff; }
QListWidget#Nav::item:hover { background: #173a5c; }
QPushButton { background: #1f6feb; color: #ffffff; border: none; border-radius: 7px;
              padding: 8px 16px; font-weight: bold; }
QPushButton:hover { background: #1a5fd0; }
QPushButton:disabled { background: #b9c5d3; color: #eef2f7; }
QPushButton#Secondary { background: transparent; color: #1f4e79; border: 1px solid #b9c7d6;
                        font-weight: normal; }
QPushButton#Secondary:hover { background: #eaf1fb; }
QPushButton#Danger { background: #b42318; }
QLineEdit, QComboBox, QDateEdit, QSpinBox { background: #ffffff; border: 1px solid #cdd6e0;
                                            border-radius: 7px; padding: 6px 9px; }
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus { border: 1px solid #1f6feb; }
QTableWidget { background: #ffffff; border: 1px solid #dee4ec; border-radius: 8px;
               gridline-color: #e8edf3; }
QHeaderView::section { background: #f7f9fc; color: #33475b; border: none; border-bottom: 1px solid #dee4ec;
                       padding: 8px; font-weight: bold; }
QProgressBar { border: 1px solid #cdd6e0; border-radius: 6px; background: #eef2f7;
               text-align: center; height: 16px; }
QProgressBar::chunk { background: #1f6feb; border-radius: 5px; }
QStatusBar { color: #4a5a6b; }
"""


def _palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#f3f5f8"))
    palette.setColor(QPalette.WindowText, QColor("#17212b"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.AlternateBase, QColor("#f7f9fc"))
    palette.setColor(QPalette.Text, QColor("#17212b"))
    palette.setColor(QPalette.Button, QColor("#ffffff"))
    palette.setColor(QPalette.ButtonText, QColor("#17212b"))
    palette.setColor(QPalette.Highlight, QColor("#1f6feb"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.PlaceholderText, QColor("#8a99a8"))
    return palette


def configure_application(app: QApplication | None = None) -> QApplication:
    """Create or configure the single QApplication used by the desktop UI."""
    if app is None:
        app = QApplication.instance()
    if app is None:
        app = QApplication([])
    if not isinstance(app, QApplication):
        raise RuntimeError(
            "Mizan's UI needs a QApplication; a QGuiApplication was already created"
        )
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setOrganizationName(APP_ORGANIZATION)
    app.setStyle("Fusion")
    app.setLayoutDirection(Qt.RightToLeft)
    app.setFont(QFont(load_bundled_fonts(), 11))
    app.setPalette(_palette())
    app.setStyleSheet(STYLESHEET)
    return app
