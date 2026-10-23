import json
from pathlib import Path

import pytest

from pdf2list.cli import _parse_pages
from pdf2list.core.extract import _apply_profile_overrides
from pdf2list.core.models import Column, ExtractionResult, Row, Table
from pdf2list.core.profiles import profile_from_dict
from pdf2list.core.settings import load_settings


def test_parse_pages():
    assert _parse_pages("5") == (5, 5)
    assert _parse_pages("3-12") == (3, 12)
    assert _parse_pages(" 3 - ") == (3, None)
    for bad in ("0", "4-2", "x", "1-a", ""):
        with pytest.raises((TypeError, ValueError)):
            _parse_pages(bad)


def test_profile_overrides_apply_to_card_rows():
    profile = profile_from_dict({
        "id": "test",
        "name": "Test",
        "product_code_overrides": {"JOLLY|JOL": "scheda corretta"},
        "description_overrides": {"COB90": "codice corretto"},
    })
    table = Table(
        columns=[Column("Prodotto", 0, 0), Column("Descrizione", 0, 0),
                 Column("Codice", 0, 0)],
        rows=[Row(["JOLLY", "vecchia", "JOL"], 1),
              Row(["ALTRO", "vecchia", "COB90"], 1)],
        pages=[1], title="Schede prodotto", source="cards",
    )
    _apply_profile_overrides(ExtractionResult("x.pdf", [table], 1), profile)
    assert table.rows[0].cells[1] == "scheda corretta"
    assert table.rows[1].cells[1] == "codice corretto"


def test_load_settings_sanitizes_bad_values(tmp_path: Path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "ocr_mode": "boh",
        "dpi": "9999",
        "ai_enabled": "true",
        "ai_max_rows": -5,
        "window_width": "x",
        "export": {"csv_delimiter": "||", "decimal_comma": "no",
                   "currency_symbol": "EURO"},
    }), encoding="utf-8")
    monkeypatch.setattr("pdf2list.core.settings.settings_path", lambda: path)
    settings = load_settings()
    assert settings.ocr_mode == "auto"
    assert settings.dpi == 600
    assert settings.ai_enabled is True
    assert settings.ai_max_rows == 10
    assert settings.window_width == 1180
    assert settings.export.csv_delimiter == ";"
    assert settings.export.decimal_comma is False
    assert settings.export.currency_symbol == "EUR"
