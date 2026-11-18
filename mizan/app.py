"""Desktop entry point: ``python -m mizan`` or the packaged ``mizan`` command."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from .bootstrap import start
from .config import AppPaths
from .ui.main_window import MainWindow
from .ui.theme import configure_application


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mizan", description="Mizan — local store accounting and inventory"
    )
    parser.add_argument(
        "--data-dir",
        help="مجلد البيانات (قاعدة البيانات والمستندات والنسخ). الافتراضي %%LOCALAPPDATA%%\\Mizan.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    configure_application(app)
    try:
        paths = AppPaths.at(args.data_dir) if args.data_dir else AppPaths.default()
        context = start(paths)
    except Exception as exc:  # Show the failure instead of an empty window.
        QMessageBox.critical(None, "ميزان", f"تعذر فتح البيانات:\n{exc}")
        return 1
    window = MainWindow(context)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
