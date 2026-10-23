"""Application entry point."""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __app_name__, __version__
from .core.settings import configure_logging, load_settings
from .ui.main_window import MainWindow
from .ui.theme import QSS

log = logging.getLogger(__name__)


def _excepthook(exc_type, exc, tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("unhandled exception:\n%s", text)
    try:
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(f"{__app_name__} - errore imprevisto")
        box.setText("Si è verificato un errore imprevisto.")
        box.setInformativeText(str(exc)[:500])
        box.setDetailedText(text[-5000:])
        box.exec()
    except Exception:
        pass


class _FileOpenFilter(QObject):
    """Open a PDF sent by Finder/Explorer (macOS QFileOpenEvent)."""

    def __init__(self, window) -> None:
        super().__init__()
        self.window = window

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.FileOpen:
            path = event.file()
            if path.lower().endswith(".pdf"):
                QTimer.singleShot(0, lambda p=path: self.window.open_pdf(p))
                return True
        return False


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("-psn_")]
    cli_flags = {"--pdf", "--xlsx", "--csv", "--ocr", "--dpi", "--profile",
                 "--profile-file", "--pages", "--password", "--ai", "--no-ai",
                 "--quiet", "--help", "-h", "--version", "--test-keyring"}
    # Accept both "--flag value" and "--flag=value".
    has_flag = any(a.split("=", 1)[0] in cli_flags for a in argv if a.startswith("-"))
    if argv and (has_flag or argv[0].lower().endswith(".pdf")):
        from .cli import main as cli_main

        return cli_main(argv)
    return run_gui()


def run_gui() -> int:
    configure_logging()
    sys.excepthook = _excepthook
    QApplication.setApplicationName(__app_name__)
    QApplication.setApplicationDisplayName(__app_name__)
    QApplication.setOrganizationName("PDF2LIST")
    QApplication.setApplicationVersion(__version__)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)
    icon = Path(__file__).resolve().parent / "resources" / "icons" / "icon_512.png"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    settings = load_settings()
    window = MainWindow(settings)
    window.show()
    app._file_open_filter = _FileOpenFilter(window)  # type: ignore[attr-defined]
    app.installEventFilter(app._file_open_filter)  # type: ignore[attr-defined]
    log.info("%s %s started", __app_name__, __version__)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
