"""Main application window."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __app_name__, __version__
from ..core.ai import provider_preset
from ..core.export import (
    configs_path_for,
    default_output_paths,
    export_configs_csv,
    export_csv,
    export_xlsx,
)
from ..core.extract import ExtractOptions
from ..core.models import ExtractionResult, Table
from ..core.normalize import parse_number
from ..core.profiles import builtin_profiles, load_profile
from ..core.settings import AppSettings, log_dir, save_settings
from .settings_dialog import SettingsDialog
from .workers import ExtractWorker

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent.parent / "resources" / "icons"


class DropZone(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setProperty("dragActive", False)
        self.setMinimumHeight(132)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)
        self.icon = QLabel("PDF")
        self.icon.setStyleSheet(
            "background:#F1F3F5;color:#5A6066;border-radius:8px;"
            "font-weight:700;font-size:11px;padding:4px 8px;"
        )
        self.icon.setFixedWidth(40)
        self.icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.icon, 0, Qt.AlignHCenter)
        self.title = QLabel("Trascina qui il listino PDF")
        self.title.setObjectName("DropTitle")
        self.title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title)
        self.hint = QLabel("oppure scegli un file dal computer")
        self.hint.setObjectName("DropHint")
        self.hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hint)
        self.button = QPushButton("Scegli PDF…")
        self.button.setCursor(Qt.PointingHandCursor)
        layout.addWidget(self.button, 0, Qt.AlignHCenter)

    def set_file(self, path: str | None) -> None:
        if path:
            name = Path(path).name
            self.title.setText(name if len(name) <= 46 else name[:43] + "…")
            self.hint.setText(f"{Path(path).parent}")
            self.button.setText("Cambia PDF…")
        else:
            self.title.setText("Trascina qui il listino PDF")
            self.hint.setText("oppure scegli un file dal computer")
            self.button.setText("Scegli PDF…")

    def set_drag(self, active: bool) -> None:
        self.setProperty("dragActive", active)
        self.style().unpolish(self)
        self.style().polish(self)


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.settings = settings
        self.result: ExtractionResult | None = None
        self.pdf_path: str | None = None
        self.worker: ExtractWorker | None = None
        self._password: str | None = None
        self._stale = False
        self._profiles: list[tuple[str, str, str]] = []
        self.setWindowTitle(f"{__app_name__} — Listini PDF in Excel e CSV")
        icon_path = ASSETS / "icon_512.png"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._build_ui()
        self._build_menu()
        self._apply_settings_to_ui()
        self._set_result_actions(False)
        self._log(f"{__app_name__} {__version__} avviato")
        self._log("L'elaborazione avviene sul tuo computer. L'AI è facoltativa.")
        self.setAcceptDrops(True)
        self.resize(max(980, settings.window_width), max(660, settings.window_height))

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 10)
        root.setSpacing(12)

        header = QFrame()
        header.setObjectName("Header")
        header.setFixedHeight(74)
        h = QHBoxLayout(header)
        h.setContentsMargins(18, 0, 16, 0)
        h.setSpacing(12)
        logo = QLabel()
        pix = QPixmap(str(ASSETS / "icon_512.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        h.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = QLabel("PDF2LIST")
        t.setObjectName("AppTitle")
        s = QLabel("Listini PDF → Excel e CSV, con AI facoltativa")
        s.setObjectName("AppSubtitle")
        titles.addWidget(t)
        titles.addWidget(s)
        h.addLayout(titles)
        h.addStretch(1)
        self.ai_badge = QLabel("Solo offline")
        self.ai_badge.setObjectName("Badge")
        h.addWidget(self.ai_badge)
        self.settings_btn = QPushButton("Impostazioni")
        self.settings_btn.setObjectName("Ghost")
        self.settings_btn.clicked.connect(self.open_settings)
        h.addWidget(self.settings_btn)
        root.addWidget(header)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)

        # ----- left column ------------------------------------------------
        left = QVBoxLayout()
        left.setSpacing(10)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(332)

        self.drop = DropZone()
        self.drop.button.clicked.connect(self.choose_pdf)
        left.addWidget(self.drop)

        options = QFrame()
        options.setObjectName("Card")
        ol = QVBoxLayout(options)
        ol.setContentsMargins(14, 14, 14, 14)
        ol.setSpacing(8)
        lab = QLabel("OPZIONI")
        lab.setObjectName("SectionLabel")
        ol.addWidget(lab)

        row = QHBoxLayout()
        row.addWidget(QLabel("Profilo"))
        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        row.addWidget(self.profile_combo, 1)
        ol.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("OCR"))
        self.ocr_combo = QComboBox()
        self.ocr_combo.addItem("Automatico", "auto")
        self.ocr_combo.addItem("Sempre", "always")
        self.ocr_combo.addItem("Mai", "never")
        self.ocr_combo.currentIndexChanged.connect(self._options_changed)
        row.addWidget(self.ocr_combo, 1)
        ol.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("DPI"))
        self.dpi_combo = QComboBox()
        for dpi in (150, 200, 250, 300, 400):
            self.dpi_combo.addItem(str(dpi), dpi)
        self.dpi_combo.currentIndexChanged.connect(self._options_changed)
        row.addWidget(self.dpi_combo, 1)
        ol.addLayout(row)

        self.ai_check = QCheckBox("Assistenza AI")
        self.ai_check.toggled.connect(self._ai_toggled)
        ol.addWidget(self.ai_check)
        self.ai_hint = QLabel("")
        self.ai_hint.setObjectName("Muted")
        self.ai_hint.setWordWrap(True)
        ol.addWidget(self.ai_hint)
        left.addWidget(options)

        self.analyze_btn = QPushButton("Analizza PDF")
        self.analyze_btn.setObjectName("Primary")
        self.analyze_btn.setMinimumHeight(38)
        self.analyze_btn.clicked.connect(self.start_extraction)
        left.addWidget(self.analyze_btn)

        export_box = QFrame()
        export_box.setObjectName("Card")
        el = QVBoxLayout(export_box)
        el.setContentsMargins(14, 14, 14, 14)
        el.setSpacing(8)
        lab2 = QLabel("ESPORTA")
        lab2.setObjectName("SectionLabel")
        el.addWidget(lab2)
        self.xlsx_btn = QPushButton("Excel (.xlsx)")
        self.xlsx_btn.setObjectName("Accent")
        self.xlsx_btn.clicked.connect(self.export_excel)
        self.csv_btn = QPushButton("CSV")
        self.csv_btn.clicked.connect(self.export_csv)
        self.folder_btn = QPushButton("Apri cartella")
        self.folder_btn.setObjectName("Ghost")
        self.folder_btn.clicked.connect(self.open_folder)
        el.addWidget(self.xlsx_btn)
        el.addWidget(self.csv_btn)
        el.addWidget(self.folder_btn)
        left.addWidget(export_box)
        left.addStretch(1)
        body.addWidget(left_widget)

        # ----- right column ----------------------------------------------
        right = QVBoxLayout()
        right.setSpacing(8)
        preview_head = QHBoxLayout()
        ptitle = QLabel("Anteprima")
        ptitle.setStyleSheet("font-size:15px;font-weight:700;color:#2E3033;")
        self.count_label = QLabel("Nessun dato")
        self.count_label.setObjectName("Muted")
        preview_head.addWidget(ptitle)
        preview_head.addWidget(self.count_label)
        preview_head.addStretch(1)
        self.preview_note = QLabel("")
        self.preview_note.setObjectName("Muted")
        preview_head.addWidget(self.preview_note)
        right.addLayout(preview_head)

        self.tabs = QTabWidget()
        self.tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.addWidget(self.tabs, 5)

        log_head = QHBoxLayout()
        log_lab = QLabel("Registro")
        log_lab.setObjectName("SectionLabel")
        log_head.addWidget(log_lab)
        log_head.addStretch(1)
        open_log = QPushButton("Apri registro")
        open_log.setObjectName("Ghost")
        open_log.clicked.connect(self.open_log)
        log_head.addWidget(open_log)
        right.addLayout(log_head)
        self.logbox = QPlainTextEdit()
        self.logbox.setObjectName("Log")
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(1200)
        self.logbox.setMinimumHeight(100)
        right.addWidget(self.logbox, 2)
        body.addLayout(right, 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self.status_label = QLabel("Pronto")
        self.status_label.setObjectName("Muted")
        footer.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(180)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        footer.addWidget(self.progress)
        self.cancel_btn = QPushButton("Annulla")
        self.cancel_btn.setObjectName("Ghost")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_work)
        footer.addWidget(self.cancel_btn)
        root.addLayout(footer)

        self._show_placeholder()

    def _build_menu(self) -> None:
        # Actions are also reachable from the window (macOS menu bar).
        self.act_open = QAction("Apri PDF…", self)
        self.act_open.setShortcut(QKeySequence.Open)
        self.act_open.triggered.connect(self.choose_pdf)
        self.act_xlsx = QAction("Esporta Excel…", self)
        self.act_xlsx.setShortcut(QKeySequence("Ctrl+E"))
        self.act_xlsx.triggered.connect(self.export_excel)
        self.act_csv = QAction("Esporta CSV…", self)
        self.act_csv.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.act_csv.triggered.connect(self.export_csv)
        self.act_settings = QAction("Impostazioni…", self)
        self.act_settings.setShortcut(QKeySequence("Ctrl+,"))
        self.act_settings.triggered.connect(self.open_settings)
        self.act_log = QAction("Apri registro", self)
        self.act_log.triggered.connect(self.open_log)
        self.act_about = QAction("Informazioni su PDF2LIST", self)
        self.act_about.triggered.connect(self.show_about)
        self.act_quit = QAction("Esci", self)
        self.act_quit.setShortcut(QKeySequence.Quit)
        self.act_quit.triggered.connect(self.close)

        bar = self.menuBar()
        file_menu = bar.addMenu("File")
        file_menu.addAction(self.act_open)
        file_menu.addSeparator()
        file_menu.addAction(self.act_xlsx)
        file_menu.addAction(self.act_csv)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)
        tools = bar.addMenu("Strumenti")
        tools.addAction(self.act_settings)
        tools.addAction(self.act_log)
        help_menu = bar.addMenu("Aiuto")
        help_menu.addAction(self.act_about)

    # ------------------------------------------------------------------
    # Settings / profiles
    # ------------------------------------------------------------------
    def _refresh_profiles(self) -> None:
        current = self.settings.profile_id
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self._profiles = builtin_profiles()
        for pid, name, _ in self._profiles:
            self.profile_combo.addItem(name, pid)
        if self.settings.custom_profile_path or self.settings.profile_id == "custom":
            self.profile_combo.addItem("Personalizzato", "custom")
        idx = self.profile_combo.findData(current)
        idx = max(idx, 0)
        self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.blockSignals(False)

    def _apply_settings_to_ui(self) -> None:
        self._refresh_profiles()
        idx = self.ocr_combo.findData(self.settings.ocr_mode)
        self.ocr_combo.setCurrentIndex(max(0, idx))
        idx = self.dpi_combo.findData(self.settings.dpi)
        if idx < 0:
            self.dpi_combo.addItem(str(self.settings.dpi), self.settings.dpi)
            idx = self.dpi_combo.findData(self.settings.dpi)
        self.dpi_combo.setCurrentIndex(max(0, idx))
        self.ai_check.blockSignals(True)
        self.ai_check.setChecked(self.settings.ai_enabled)
        self.ai_check.blockSignals(False)
        self._update_ai_hint()

    def _update_ai_hint(self) -> None:
        if not self.settings.ai_enabled:
            self.ai_badge.setText("Solo offline")
            self.ai_hint.setText("Nessun dato lascia il computer.")
            return
        preset = provider_preset(self.settings.provider)
        model = self.settings.model or preset.default_model
        self.ai_badge.setText("AI attiva")
        try:
            settings = self.settings.ai_settings()
            key = settings.api_key
        except Exception:
            key = ""
        if preset.needs_key and not key:
            self.ai_hint.setText(
                f"{preset.name} · {model}\nChiave API mancante: apri Impostazioni → AI."
            )
        else:
            self.ai_hint.setText(
                f"{preset.name} · {model}\nI dati estratti vengono inviati al provider."
            )

    def _profile_changed(self) -> None:
        pid = self.profile_combo.currentData()
        if not pid:
            return
        self.settings.profile_id = pid
        save_settings(self.settings)
        self._mark_stale()

    def _options_changed(self) -> None:
        self.settings.ocr_mode = self.ocr_combo.currentData() or "auto"
        self.settings.dpi = int(self.dpi_combo.currentData() or 200)
        save_settings(self.settings)
        self._mark_stale()

    def _ai_toggled(self, checked: bool) -> None:
        self.settings.ai_enabled = checked
        save_settings(self.settings)
        self._update_ai_hint()
        self._mark_stale()

    def _mark_stale(self) -> None:
        if self.result is not None and not (self.worker and self.worker.isRunning()):
            self._stale = True
            self.status_label.setText("Opzioni modificate — rianalizza il PDF")

    def _current_profile(self):
        path = self.settings.custom_profile_path if self.settings.profile_id == "custom" else None
        try:
            return load_profile(self.settings.profile_id, path)
        except Exception as exc:
            self._log(f"Profilo non valido ({exc}); uso il profilo Generico.")
            return load_profile("generic")

    # ------------------------------------------------------------------
    # File / extraction
    # ------------------------------------------------------------------
    def choose_pdf(self) -> None:
        start = self.settings.last_dir or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Scegli un listino PDF", start,
                                              "PDF (*.pdf)")
        if path:
            self.open_pdf(path)

    def open_pdf(self, path: str) -> None:
        if not Path(path).is_file():
            QMessageBox.warning(self, "PDF2LIST", "Il file selezionato non esiste.")
            return
        self.pdf_path = str(Path(path))
        self._password = None
        self.settings.last_dir = str(Path(path).parent)
        save_settings(self.settings)
        self.drop.set_file(self.pdf_path)
        self._log(f"PDF selezionato: {self.pdf_path}")
        self.start_extraction()

    def start_extraction(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        if not self.pdf_path:
            self.choose_pdf()
            return
        if self._stale:
            self._stale = False
        profile = self._current_profile()
        options = ExtractOptions(
            ocr_mode=self.settings.ocr_mode,
            dpi=int(self.settings.dpi),
            profile_id=self.settings.profile_id,
            profile_path=(self.settings.custom_profile_path
                          if self.settings.profile_id == "custom" else None),
            password=self._password,
        )
        ai_settings = self.settings.ai_settings() if self.settings.ai_enabled else None
        if ai_settings:
            preset = provider_preset(ai_settings.provider)
            if preset.needs_key and not ai_settings.api_key:
                QMessageBox.warning(
                    self, "AI non configurata",
                    "L'assistenza AI è attiva ma manca la chiave API.\n"
                    "L'elaborazione proseguirà senza AI. Puoi inserire la chiave "
                    "in Impostazioni → AI."
                )
                ai_settings = None
        self._reset_for_run()
        self._log("—" * 46)
        self._log(f"Analisi avviata con profilo «{profile.name}» "
                  f"(OCR {options.ocr_mode}, {options.dpi} DPI)")
        if ai_settings:
            preset = provider_preset(ai_settings.provider)
            self._log(f"AI attiva: {preset.name} · {ai_settings.model or preset.default_model}")
        self._set_busy(True)
        self.worker = ExtractWorker(self.pdf_path, options, profile, ai_settings, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.password_required.connect(self._on_password_required)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.finished.connect(self._worker_done)
        self.worker.start()

    def _reset_for_run(self) -> None:
        self.result = None
        self._set_result_actions(False)
        self._clear_tabs()
        self._show_placeholder()
        self.count_label.setText("Analisi in corso…")
        self.preview_note.setText("")
        self.progress.setRange(0, 0)
        self.progress.setValue(0)

    def _set_busy(self, busy: bool) -> None:
        self.analyze_btn.setEnabled(not busy)
        self.analyze_btn.setText("Analisi in corso…" if busy else "Analizza PDF")
        self.settings_btn.setEnabled(not busy)
        self.profile_combo.setEnabled(not busy)
        self.ocr_combo.setEnabled(not busy)
        self.dpi_combo.setEnabled(not busy)
        self.ai_check.setEnabled(not busy)
        self.cancel_btn.setVisible(busy)
        self.cancel_btn.setEnabled(busy)
        self.cancel_btn.setText("Annulla")
        self.drop.setEnabled(not busy)
        self.act_open.setEnabled(not busy)
        self.act_settings.setEnabled(not busy)

    def _set_result_actions(self, enabled: bool) -> None:
        self.xlsx_btn.setEnabled(enabled)
        self.csv_btn.setEnabled(enabled)
        self.act_xlsx.setEnabled(enabled)
        self.act_csv.setEnabled(enabled)
        self.folder_btn.setEnabled(enabled or bool(self.pdf_path))

    def _on_progress(self, message: str, current: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(current, total))
        else:
            self.progress.setRange(0, 0)
        self.status_label.setText(message)
        if message and (message.startswith(("Analisi", "Pagina", "AI", "OCR"))):
            self._log(message)

    def _on_finished(self, result: ExtractionResult) -> None:
        self.result = result
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self._set_busy(False)
        self._set_result_actions(bool(result.tables))
        self._build_preview(result)
        ntab = len(result.tables)
        nrows = result.row_count
        ncfg = len(result.meta.get("configs") or [])
        self.count_label.setText(
            f"{ntab} tabelle · {nrows} righe" + (f" · {ncfg} configurazioni" if ncfg else "")
        )
        self.status_label.setText(f"Completato: {nrows} righe estratte")
        self._log(f"Estrazione completata: {ntab} tabelle, {nrows} righe.")
        if ncfg:
            self._log(f"Configurazioni dai badge: {ncfg}")
        for w in result.warnings[:8]:
            self._log(f"Avviso: {w}")
        if not result.tables:
            QMessageBox.information(
                self, "Nessuna tabella",
                "Non è stato possibile riconoscere una tabella di prezzi.\n\n"
                "Suggerimenti:\n"
                "• prova OCR “Sempre” e 300 DPI;\n"
                "• seleziona il profilo adatto al listino;\n"
                "• attiva AI Vision nelle impostazioni per le pagine complesse."
            )

    def _on_failed(self, message: str, tb: str) -> None:
        self._set_busy(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status_label.setText("Errore")
        self._log(f"ERRORE: {message}")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("Errore di elaborazione")
        box.setText("Non è stato possibile elaborare il PDF.")
        box.setInformativeText(message[:600])
        box.setDetailedText(tb[-4000:])
        box.exec()

    def _on_password_required(self) -> None:
        self._set_busy(False)
        text, ok = QInputDialog.getText(
            self, "PDF protetto", "Il PDF è protetto da password.\nPassword:",
            QLineEdit.Password,
        )
        if ok and text:
            self._password = text
            QTimer.singleShot(50, self.start_extraction)
        else:
            self.status_label.setText("Operazione annullata")
            self._log("PDF protetto: password non fornita.")

    def _on_cancelled(self) -> None:
        self._set_busy(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status_label.setText("Annullato")
        self._log("Operazione annullata dall'utente.")

    def _worker_done(self) -> None:
        if self.worker:
            self.worker.deleteLater()
        self.worker = None

    def cancel_work(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("Annullamento…")
            self.status_label.setText("Annullamento in corso…")

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------
    def _show_placeholder(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setAlignment(Qt.AlignCenter)
        title = QLabel("Nessun PDF analizzato")
        title.setStyleSheet("font-size:18px;font-weight:600;color:#5A6066;")
        sub = QLabel("Apri un listino per vedere qui l'anteprima delle tabelle estratte.")
        sub.setObjectName("Muted")
        lay.addWidget(title, 0, Qt.AlignCenter)
        lay.addWidget(sub, 0, Qt.AlignCenter)
        self.tabs.addTab(w, "Anteprima")

    def _clear_tabs(self) -> None:
        while self.tabs.count():
            widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            widget.deleteLater()

    def _build_preview(self, result: ExtractionResult) -> None:
        self._clear_tabs()
        tables = [t for t in result.tables if t.data_rows()]
        if not tables:
            self._show_placeholder()
            self.preview_note.setText("")
            return
        truncated = False
        for ti, table in enumerate(tables):
            widget = self._table_widget(table)
            name = table.title or f"Tabella {ti + 1}"
            if len(name) > 30:
                name = name[:29] + "…"
            label = f"{name} · {len(table.data_rows())}"
            self.tabs.addTab(widget, label)
            self.tabs.setTabToolTip(ti, f"{table.title}\nOrigine: {table.source}\n"
                                        f"Pagine: {', '.join(map(str, table.pages[:12]))}")
            truncated = truncated or len(table.data_rows()) > 800
        self.preview_note.setText("anteprima max 800 righe per tabella" if truncated else "")
        self.tabs.setCurrentIndex(0)

    def _table_widget(self, table: Table) -> QTableWidget:
        headers = list(table.headers)
        if not headers:
            headers = ["Dati"]
        page_col = self.settings.export.include_page_column
        ncols = len(headers) + (1 if page_col else 0)
        rows = table.rows
        show = rows[:800]
        tw = QTableWidget(len([r for r in show if r.kind == "data"]), ncols)
        tw.setHorizontalHeaderLabels(headers + (["Pagina"] if page_col else []))
        tw.verticalHeader().setVisible(False)
        tw.setWordWrap(False)
        tw.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tw.setSelectionMode(QAbstractItemView.SingleSelection)
        tw.setAlternatingRowColors(False)
        tw.setUpdatesEnabled(False)
        ri = 0
        for row in show:
            if row.kind == "group":
                # group headings are rendered inside the previous row block:
                # use a full-width merged row instead of a separate empty row
                tw.insertRow(ri)
                text = row.cells[0] if row.cells else row.group
                item = QTableWidgetItem(text)
                font = item.font()
                font.setBold(True)
                item.setFont(font)
                item.setBackground(QColor("#E3E7EA"))
                item.setForeground(QColor("#1C2226"))
                tw.setItem(ri, 0, item)
                if ncols > 1:
                    tw.setSpan(ri, 0, 1, ncols)
                tw.setRowHeight(ri, 24)
                ri += 1
                continue
            for ci in range(len(headers)):
                raw = row.cells[ci] if ci < len(row.cells) else ""
                item = QTableWidgetItem(str(raw))
                item.setForeground(QColor("#232629"))
                col = table.columns[ci] if ci < len(table.columns) else None
                if col and col.kind in {"price", "number"} and parse_number(raw) is not None:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                tw.setItem(ri, ci, item)
            if page_col:
                item = QTableWidgetItem(str(row.page))
                item.setForeground(QColor("#5A6066"))
                item.setTextAlignment(Qt.AlignCenter)
                tw.setItem(ri, len(headers), item)
            ri += 1
        tw.setUpdatesEnabled(True)
        tw.resizeColumnsToContents()
        for ci in range(ncols):
            width = tw.columnWidth(ci)
            tw.setColumnWidth(ci, max(70, min(width + 10, 300)))
        if ncols:
            tw.setColumnWidth(0, min(max(160, tw.columnWidth(0)), 420))
        tw.horizontalHeader().setStretchLastSection(False)
        tw.setShowGrid(False)
        # Explicit colours: on macOS dark mode the inherited text colour is
        # light, which made the table unreadable on its white background.
        tw.setStyleSheet(
            "QTableWidget { background:#FFFFFF; color:#232629; border:none; }"
            "QTableWidget::item { color:#232629; padding:4px 6px; }"
            "QTableWidget::item:selected { background:#CDEFE8; color:#16332E; }"
        )
        return tw

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_excel(self) -> None:
        if not self.result or not self.result.tables:
            QMessageBox.information(self, "PDF2LIST", "Analizza prima un PDF.")
            return
        xlsx, _ = default_output_paths(self.pdf_path or "listino.pdf")
        start = Path(self.settings.last_dir) / xlsx.name if self.settings.last_dir else xlsx
        path, _ = QFileDialog.getSaveFileName(
            self, "Salva Excel", str(start), "Excel (*.xlsx)"
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            export_xlsx(self.result, path, self.settings)
        except Exception as exc:
            log.exception("xlsx export failed")
            QMessageBox.critical(self, "Errore", f"Esportazione Excel non riuscita:\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.settings.last_dir = str(Path(path).parent)
        save_settings(self.settings)
        self._log(f"Excel salvato: {path}")
        self.status_label.setText("Excel salvato")
        if self.settings.export.open_after_export:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            QMessageBox.information(self, "PDF2LIST", f"Excel salvato:\n{path}")

    def export_csv(self) -> None:
        if not self.result or not self.result.tables:
            QMessageBox.information(self, "PDF2LIST", "Analizza prima un PDF.")
            return
        _, csvp = default_output_paths(self.pdf_path or "listino.pdf")
        start = Path(self.settings.last_dir) / csvp.name if self.settings.last_dir else csvp
        path, _ = QFileDialog.getSaveFileName(
            self, "Salva CSV", str(start), "CSV (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            export_csv(self.result, path, self.settings)
            cfg_path = configs_path_for(path)
            cfg = export_configs_csv(self.result, cfg_path, self.settings)
        except Exception as exc:
            log.exception("csv export failed")
            QMessageBox.critical(self, "Errore", f"Esportazione CSV non riuscita:\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.settings.last_dir = str(Path(path).parent)
        save_settings(self.settings)
        self._log(f"CSV salvato: {path}")
        if cfg:
            self._log(f"Configurazioni salvate: {cfg}")
        self.status_label.setText("CSV salvato")
        if self.settings.export.open_after_export:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            msg = f"CSV salvato:\n{path}"
            if cfg:
                msg += f"\n\nConfigurazioni:\n{cfg}"
            QMessageBox.information(self, "PDF2LIST", msg)

    def open_folder(self) -> None:
        folder = None
        if self.settings.last_dir and Path(self.settings.last_dir).is_dir():
            folder = self.settings.last_dir
        elif self.pdf_path:
            folder = str(Path(self.pdf_path).parent)
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def open_log(self) -> None:
        path = log_dir() / "pdf2list.log"
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QMessageBox.information(self, "PDF2LIST", "Il registro è ancora vuoto.")

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    def open_settings(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.settings
            save_settings(self.settings)
            self._apply_settings_to_ui()
            self._mark_stale()
            self._log("Impostazioni aggiornate.")
            if self.settings.ai_enabled:
                preset = provider_preset(self.settings.provider)
                self._log(f"AI: {preset.name} · {self.settings.model or preset.default_model}")

    def show_about(self) -> None:
        QMessageBox.about(
            self, f"Informazioni su {__app_name__}",
            f"<h3>{__app_name__} {__version__}</h3>"
            "<p>Trasforma listini PDF in Excel e CSV, su macOS (Intel e Apple "
            "Silicon) e Windows.</p>"
            "<p>OCR offline con RapidOCR. L'assistenza AI è facoltativa e "
            "richiede una chiave API del provider scelto; le chiavi sono "
            "conservate nel portachiavi di sistema.</p>"
            "<p>Verifica sempre prezzi e codici prima dell'uso commerciale.</p>"
        )

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logbox.appendPlainText(f"{stamp}  {message}")
        bar = self.logbox.verticalScrollBar()
        bar.setValue(bar.maximum())

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".pdf"):
                    event.acceptProposedAction()
                    self.drop.set_drag(True)
                    return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.drop.set_drag(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self.drop.set_drag(False)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".pdf"):
                self.open_pdf(path)
                event.acceptProposedAction()
                return

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            answer = QMessageBox.question(
                self, "Elaborazione in corso",
                "Annullare l'elaborazione e uscire?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.cancel()
            if not self.worker.wait(15000):
                QMessageBox.information(
                    self, "Attendere",
                    "L'operazione è ancora in corso (ad esempio una richiesta AI). "
                    "Riprova a chiudere tra qualche secondo.",
                )
                event.ignore()
                return
        self.settings.window_width = self.width()
        self.settings.window_height = self.height()
        save_settings(self.settings)
        event.accept()
