"""Settings dialog: OCR, profiles, AI provider and export defaults."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.ai import AIError, AISettings, PROVIDERS, provider_preset
from ..core.profiles import builtin_profiles, save_profile_template
from ..core.settings import AppSettings, get_api_key, save_settings, set_api_key
from .workers import TestAIWorker


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = copy.deepcopy(settings)
        self._test_worker: TestAIWorker | None = None
        self.setWindowTitle("Impostazioni — PDF2LIST")
        self.setMinimumSize(680, 620)
        self._build()
        self._load()
        self._update_provider_fields(initial=True)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(14)

        title = QLabel("Impostazioni")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #2E3033;")
        root.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._tab_general(), "Generale")
        tabs.addTab(self._tab_ai(), "AI")
        tabs.addTab(self._tab_export(), "Esportazione")
        root.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Salva")
        buttons.button(QDialogButtonBox.Cancel).setText("Annulla")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _tab_general(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(16, 18, 16, 16)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignLeft)

        self.ocr_mode = QComboBox()
        self.ocr_mode.addItem("Automatico (OCR solo se il PDF non ha testo)", "auto")
        self.ocr_mode.addItem("Sempre OCR (PDF scansionati / immagini)", "always")
        self.ocr_mode.addItem("Mai OCR (solo PDF con testo)", "never")
        form.addRow("Modalità OCR", self.ocr_mode)

        self.dpi = QComboBox()
        for dpi in (150, 200, 250, 300, 400):
            self.dpi.addItem(f"{dpi} DPI", dpi)
        form.addRow("Risoluzione OCR", self.dpi)

        note = QLabel(
            "L'OCR integrato (RapidOCR) funziona offline su Windows, Mac Intel e "
            "Apple Silicon. 200 DPI è un buon compromesso; usa 300 DPI per "
            "caratteri molto piccoli."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        form.addRow("", note)

        self.profile = QComboBox()
        self._profiles = builtin_profiles()
        for pid, name, desc in self._profiles:
            self.profile.addItem(name, pid)
        self.profile.addItem("Personalizzato (file JSON)…", "custom")
        form.addRow("Profilo di estrazione", self.profile)

        self.profile_desc = QLabel("")
        self.profile_desc.setObjectName("Muted")
        self.profile_desc.setWordWrap(True)
        form.addRow("", self.profile_desc)

        prow = QHBoxLayout()
        self.custom_profile = QLineEdit()
        self.custom_profile.setPlaceholderText("Nessun profilo personalizzato")
        browse = QPushButton("Scegli…")
        browse.clicked.connect(self._browse_profile)
        template = QPushButton("Crea modello…")
        template.setObjectName("Ghost")
        template.clicked.connect(self._save_profile_template)
        prow.addWidget(self.custom_profile, 1)
        prow.addWidget(browse)
        prow.addWidget(template)
        form.addRow("Profilo JSON", prow)

        self.profile.currentIndexChanged.connect(self._profile_changed)
        return w

    def _tab_ai(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.ai_enabled = QCheckBox("Abilita assistenza AI (facoltativa)")
        layout.addWidget(self.ai_enabled)

        privacy = QLabel(
            "L'AI è disattivata per impostazione predefinita. Quando la attivi, "
            "il testo estratto (o l'immagine della pagina, solo se usi AI Vision) "
            "viene inviato al provider scelto. Le chiavi sono salvate nel "
            "portachiavi di sistema, mai nel file delle impostazioni."
        )
        privacy.setObjectName("Muted")
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        box = QGroupBox("Provider")
        form = QFormLayout(box)
        form.setSpacing(10)
        self.provider = QComboBox()
        for p in PROVIDERS:
            self.provider.addItem(p.name, p.id)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        form.addRow("Provider", self.provider)

        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://…/v1")
        form.addRow("URL base", self.base_url)

        self.model = QLineEdit()
        self.model.setPlaceholderText("es. gpt-4o-mini")
        form.addRow("Modello", self.model)

        key_row = QHBoxLayout()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("Inserisci la chiave API")
        self.show_key = QPushButton("Mostra")
        self.show_key.setObjectName("Ghost")
        self.show_key.setCheckable(True)
        self.show_key.toggled.connect(self._toggle_key)
        key_row.addWidget(self.api_key, 1)
        key_row.addWidget(self.show_key)
        form.addRow("Chiave API", key_row)

        test_row = QHBoxLayout()
        self.test_btn = QPushButton("Prova connessione")
        self.test_btn.clicked.connect(self._test)
        self.delete_key_btn = QPushButton("Elimina chiave salvata")
        self.delete_key_btn.setObjectName("Ghost")
        self.delete_key_btn.clicked.connect(self._delete_key)
        self.test_status = QLabel("")
        self.test_status.setObjectName("Muted")
        self.test_status.setWordWrap(True)
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.delete_key_btn)
        test_row.addWidget(self.test_status, 1)
        form.addRow("", test_row)

        self.provider_note = QLabel("")
        self.provider_note.setObjectName("Muted")
        self.provider_note.setWordWrap(True)
        form.addRow("", self.provider_note)
        layout.addWidget(box)

        tasks = QGroupBox("Attività AI")
        tform = QFormLayout(tasks)
        self.ai_map = QCheckBox("Mappa le intestazioni sui campi standard")
        self.ai_clean = QCheckBox("Correggi spazi ed errori OCR nelle righe")
        self.ai_vision = QCheckBox("Usa AI Vision per le pagine senza tabelle")
        tform.addRow(self.ai_map)
        tform.addRow(self.ai_clean)
        tform.addRow(self.ai_vision)
        self.ai_max = QSpinBox()
        self.ai_max.setRange(10, 3000)
        self.ai_max.setSingleStep(50)
        self.ai_max.setSuffix(" righe")
        tform.addRow("Limite righe da correggere", self.ai_max)
        self.ai_timeout = QSpinBox()
        self.ai_timeout.setRange(10, 300)
        self.ai_timeout.setSuffix(" s")
        tform.addRow("Timeout richiesta", self.ai_timeout)
        warn = QLabel("L'AI può introdurre errori: controlla sempre prezzi e codici.")
        warn.setObjectName("Warning")
        warn.setWordWrap(True)
        tform.addRow(warn)
        layout.addWidget(tasks)
        layout.addStretch(1)
        return w

    def _tab_export(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        box = QGroupBox("CSV")
        form = QFormLayout(box)
        self.delimiter = QComboBox()
        self.delimiter.addItem("Punto e virgola ( ; ) — Excel italiano", ";")
        self.delimiter.addItem("Virgola ( , )", ",")
        self.delimiter.addItem("Tab ( \\t )", "\t")
        form.addRow("Separatore", self.delimiter)
        self.decimal_comma = QCheckBox("Decimali con virgola (1.234,50)")
        form.addRow("", self.decimal_comma)
        layout.addWidget(box)

        box2 = QGroupBox("Excel")
        form2 = QFormLayout(box2)
        self.one_sheet = QCheckBox("Un foglio per ogni tabella")
        self.combined = QCheckBox("Aggiungi il foglio 'Tutti i dati'")
        self.page_col = QCheckBox("Aggiungi la colonna 'Pagina'")
        self.open_after = QCheckBox("Apri il file al termine dell'esportazione")
        self.currency = QLineEdit()
        self.currency.setMaxLength(3)
        form2.addRow(self.one_sheet)
        form2.addRow(self.combined)
        form2.addRow(self.page_col)
        form2.addRow(self.open_after)
        form2.addRow("Simbolo valuta", self.currency)
        layout.addWidget(box2)
        layout.addStretch(1)
        return w

    # ------------------------------------------------------------------
    def _load(self) -> None:
        s = self.settings
        idx = self.ocr_mode.findData(s.ocr_mode)
        self.ocr_mode.setCurrentIndex(max(0, idx))
        idx = self.dpi.findData(s.dpi)
        self.dpi.setCurrentIndex(max(0, idx))
        pidx = self.profile.findData(s.profile_id)
        if pidx < 0:
            pidx = self.profile.findData("custom")
        self.profile.setCurrentIndex(max(0, pidx))
        self.custom_profile.setText(s.custom_profile_path)
        self._profile_changed()

        self.ai_enabled.setChecked(s.ai_enabled)
        idx = self.provider.findData(s.provider)
        self.provider.setCurrentIndex(max(0, idx))
        self.base_url.setText(s.base_url)
        self.model.setText(s.model)
        self.ai_map.setChecked(s.ai_map_columns)
        self.ai_clean.setChecked(s.ai_clean_rows)
        self.ai_vision.setChecked(s.ai_vision_fallback)
        self.ai_max.setValue(max(10, s.ai_max_rows))
        self.ai_timeout.setValue(max(10, s.ai_timeout))

        self.delimiter.setCurrentIndex(max(0, self.delimiter.findData(s.export.csv_delimiter)))
        self.decimal_comma.setChecked(s.export.decimal_comma)
        self.one_sheet.setChecked(s.export.one_sheet_per_table)
        self.combined.setChecked(s.export.combined_sheet)
        self.page_col.setChecked(s.export.include_page_column)
        self.open_after.setChecked(s.export.open_after_export)
        self.currency.setText(s.export.currency_symbol)

    def _profile_changed(self) -> None:
        pid = self.profile.currentData()
        desc = ""
        for p, name, d in self._profiles:
            if p == pid:
                desc = d
                break
        if pid == "custom":
            desc = "Usa un file JSON creato da te."
            self.custom_profile.setEnabled(True)
        else:
            self.custom_profile.setEnabled(False)
        self.profile_desc.setText(desc)

    def _browse_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Scegli profilo JSON", self.custom_profile.text() or str(Path.home()),
            "Profili JSON (*.json)"
        )
        if path:
            self.custom_profile.setText(path)
            self.profile.setCurrentIndex(self.profile.findData("custom"))

    def _save_profile_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Crea un profilo di esempio", str(Path.home() / "mio-profilo.json"),
            "Profili JSON (*.json)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            save_profile_template(path)
            QMessageBox.information(
                self, "Profilo creato",
                f"Modello di profilo salvato in:\n{path}\n\n"
                "Modifica sinonimi, correzioni e override, poi selezionalo con «Scegli…»."
            )
        except Exception as exc:
            QMessageBox.warning(self, "Profilo", f"Impossibile creare il file:\n{exc}")

    def _provider_changed(self) -> None:
        self._update_provider_fields()

    def _update_provider_fields(self, initial: bool = False) -> None:
        pid = self.provider.currentData() or "openai"
        preset = provider_preset(pid)
        if initial:
            if not self.base_url.text().strip():
                self.base_url.setText(preset.base_url)
            if not self.model.text().strip():
                self.model.setText(preset.default_model)
        elif pid != "custom":
            self.base_url.setText(preset.base_url)
            self.model.setText(preset.default_model)
            self.api_key.clear()
            self.test_status.setText("")
        self.provider_note.setText(preset.note)
        key = get_api_key(pid)
        needs = preset.needs_key
        self.api_key.setEnabled(needs)
        self.delete_key_btn.setEnabled(bool(key) and needs)
        if key:
            self.api_key.setPlaceholderText("Chiave salvata nel portachiavi (lascia vuoto per mantenere)")
        elif needs:
            self.api_key.setPlaceholderText("Inserisci la chiave API")
        else:
            self.api_key.setPlaceholderText("Non richiesta")
        self.base_url.setEnabled(pid == "custom" or pid in {"ollama", "lmstudio"})
        if pid == "custom":
            self.base_url.setEnabled(True)
            self.base_url.setReadOnly(False)
        else:
            self.base_url.setReadOnly(True)

    def _toggle_key(self, visible: bool) -> None:
        self.api_key.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        self.show_key.setText("Nascondi" if visible else "Mostra")

    def _ai_settings(self) -> AISettings:
        pid = self.provider.currentData() or "openai"
        key = self.api_key.text().strip() or get_api_key(pid)
        return AISettings(
            enabled=self.ai_enabled.isChecked(),
            provider=pid,
            base_url=self.base_url.text().strip(),
            model=self.model.text().strip(),
            api_key=key,
            timeout=self.ai_timeout.value(),
            map_columns=self.ai_map.isChecked(),
            clean_rows=self.ai_clean.isChecked(),
            vision_fallback=self.ai_vision.isChecked(),
            max_rows=self.ai_max.value(),
        )

    def _test(self) -> None:
        if self._test_worker and self._test_worker.isRunning():
            return
        settings = self._ai_settings()
        preset = provider_preset(settings.provider)
        if preset.needs_key and not settings.api_key:
            self.test_status.setText("Inserisci prima una chiave API.")
            return
        self.test_btn.setEnabled(False)
        self.test_status.setText("Verifica in corso…")
        self._test_worker = TestAIWorker(settings, self)
        self._test_worker.ok.connect(self._test_ok)
        self._test_worker.failed.connect(self._test_failed)
        self._test_worker.finished.connect(lambda: self.test_btn.setEnabled(True))
        self._test_worker.start()

    def _test_ok(self, text: str) -> None:
        self.test_status.setStyleSheet("color: #007D70;")
        self.test_status.setText(f"Connessione riuscita: {text[:80]}")

    def _test_failed(self, message: str) -> None:
        self.test_status.setStyleSheet("color: #B03A2E;")
        self.test_status.setText(message[:300])

    def _delete_key(self) -> None:
        pid = self.provider.currentData() or "openai"
        ok, msg = set_api_key(pid, "")
        self.api_key.clear()
        self.api_key.setPlaceholderText("Inserisci la chiave API")
        self.delete_key_btn.setEnabled(False)
        self.test_status.setText(msg if not ok else "Chiave eliminata.")

    # ------------------------------------------------------------------
    def _apply(self) -> None:
        s = self.settings
        s.ocr_mode = self.ocr_mode.currentData()
        s.dpi = int(self.dpi.currentData())
        s.profile_id = self.profile.currentData()
        s.custom_profile_path = self.custom_profile.text().strip()
        s.ai_enabled = self.ai_enabled.isChecked()
        s.provider = self.provider.currentData() or "openai"
        s.base_url = self.base_url.text().strip()
        s.model = self.model.text().strip()
        s.ai_map_columns = self.ai_map.isChecked()
        s.ai_clean_rows = self.ai_clean.isChecked()
        s.ai_vision_fallback = self.ai_vision.isChecked()
        s.ai_max_rows = self.ai_max.value()
        s.ai_timeout = self.ai_timeout.value()
        s.export.csv_delimiter = self.delimiter.currentData() or ";"
        s.export.decimal_comma = self.decimal_comma.isChecked()
        s.export.one_sheet_per_table = self.one_sheet.isChecked()
        s.export.combined_sheet = self.combined.isChecked()
        s.export.include_page_column = self.page_col.isChecked()
        s.export.open_after_export = self.open_after.isChecked()
        s.export.currency_symbol = self.currency.text().strip() or "€"

        key = self.api_key.text().strip()
        if key:
            ok, msg = set_api_key(s.provider, key)
            if not ok:
                QMessageBox.warning(self, "Chiave API", msg)
        if s.ai_enabled and s.provider == "custom" and (not s.base_url or not s.model):
            raise ValueError("Per il provider personalizzato inserisci URL base e modello.")
        if s.profile_id == "custom" and not s.custom_profile_path:
            raise ValueError("Seleziona un file JSON per il profilo personalizzato.")
        save_settings(s)

    def _accept(self) -> None:
        try:
            self._apply()
        except ValueError as exc:
            QMessageBox.warning(self, "Impostazioni", str(exc))
            return
        self.accept()

    def closeEvent(self, event) -> None:
        if self._test_worker and self._test_worker.isRunning():
            self._test_worker.wait(1500)
        super().closeEvent(event)
