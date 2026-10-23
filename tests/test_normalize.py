from pdf2list.core.normalize import (
    canonical_header,
    column_kind,
    parse_number,
    safe_filename,
    tidy,
)
from pdf2list.core.profiles import load_profile


def test_parse_number_italian_and_english():
    assert parse_number("€ 1.234,50") == 1234.50
    assert parse_number("1.234,50 €") == 1234.50
    assert parse_number("1,234.50") == 1234.50
    assert parse_number("36,00") == 36.0
    assert parse_number("0,6 kg") == 0.6
    assert parse_number("7") == 7.0
    assert parse_number("-") is None
    assert parse_number("") is None
    assert parse_number("1.234") == 1234.0
    assert parse_number("12.50") == 12.5
    assert parse_number("COB90") is None
    assert parse_number("BRACCIOLO 01") is None
    assert parse_number("€ 52,00 | € 55,00") is None
    assert parse_number("110x62x83") is None


def test_headers_multilingual():
    assert canonical_header("Prezzo Unitario") == "Prezzo"
    assert canonical_header("Precio") == "Prezzo"
    assert canonical_header("Cod. articolo") == "Codice"
    assert canonical_header("Peso (kg)") == "Kg"
    assert canonical_header("Description") == "Descrizione"
    assert canonical_header("Misure") == "Misure"
    assert canonical_header("Colore struttura") == "Struttura"
    assert canonical_header("Comp.") == "Componenti"


def test_column_kind():
    assert column_kind("Prezzo €", ["1,00"]) == "price"
    assert column_kind("Kg", ["1,2"]) == "number"
    assert column_kind("Descrizione", ["Panca"]) == "text"


def test_profile_fixes_and_overrides():
    p = load_profile("fernova")
    assert p.name
    assert p.canonical("Modello") == "Codice"
    assert p.canonical("Formati") == "Formato"
    assert p.fix("Panca con monbe in metallo") == "Panca con gambe in metallo"
    assert p.apply_description_overrides("X", "MECV32", "brutta") == (
        "Colonna verniciata Ø60 mm h. 32 cm + viti."
    )
    assert p.apply_section_fix("APPENDIABIII") == "APPENDIABITI"


def test_safe_filename():
    assert safe_filename("Listino 2023/2026") == "Listino 2023-2026"
