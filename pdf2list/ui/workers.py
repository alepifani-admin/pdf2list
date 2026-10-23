"""Background workers (extraction, AI test)."""

from __future__ import annotations

import logging
import threading
import traceback

from PySide6.QtCore import QThread, Signal

from ..core.ai import AIClient, AIError, AISettings, enhance_result
from ..core.extract import ExtractionCancelled, ExtractOptions, extract_pdf
from ..core.pdftext import PdfPasswordRequired
from ..core.profiles import Profile

log = logging.getLogger(__name__)


class ExtractWorker(QThread):
    progress = Signal(str, int, int)
    finished_ok = Signal(object)
    failed = Signal(str, str)
    password_required = Signal()
    cancelled = Signal()

    def __init__(self, path: str, options: ExtractOptions, profile: Profile,
                 ai_settings: AISettings | None, parent=None) -> None:
        super().__init__(parent)
        self.path = path
        self.options = options
        self.profile = profile
        self.ai_settings = ai_settings
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def _progress(self, message: str, current: int, total: int) -> None:
        self.progress.emit(message, current, total)

    def run(self) -> None:
        try:
            result = extract_pdf(
                self.path,
                self.options,
                profile=self.profile,
                progress=self._progress,
                cancel=self._cancel.is_set,
            )
        except PdfPasswordRequired:
            self.password_required.emit()
            return
        except ExtractionCancelled:
            self.cancelled.emit()
            return
        except Exception as exc:
            tb = traceback.format_exc()
            log.error("extraction failed: %s\n%s", exc, tb)
            self.failed.emit(str(exc), tb)
            return

        if self._cancel.is_set():
            self.cancelled.emit()
            return

        if self.ai_settings and self.ai_settings.enabled:
            try:
                result = enhance_result(
                    result,
                    self.ai_settings,
                    self.profile,
                    progress=self._progress,
                    pdf_path=self.path,
                )
            except AIError as exc:
                result.warnings.append(f"AI: {exc}")
                log.warning("AI failed: %s", exc)
            except Exception as exc:
                result.warnings.append(f"AI: errore imprevisto ({exc})")
                log.exception("AI unexpected failure")

        if self._cancel.is_set():
            self.cancelled.emit()
            return
        self.finished_ok.emit(result)


class TestAIWorker(QThread):
    ok = Signal(str)
    failed = Signal(str)

    def __init__(self, settings: AISettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings

    def run(self) -> None:
        try:
            client = AIClient(self.settings)
            text = client.test()
            self.ok.emit(text or "OK")
        except Exception as exc:
            self.failed.emit(str(exc))
