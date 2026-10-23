import csv
from pathlib import Path

from openpyxl import load_workbook

from pdf2list.core.export import export_configs_csv, export_csv, export_xlsx
from pdf2list.core.models import Column, ExtractionResult, Row, Table
from pdf2list.core.settings import AppSettings


def sample_result() -> ExtractionResult:
    cols = [
        Column("Descrizione", 0, 100, kind="text"),
        Column("Codice", 0, 0, kind="text"),
        Column("Kg", 0, 0, kind="number"),
        Column("Prezzo", 0, 0, kind="price"),
    ]
    rows = [
        Row(["FIANCHI", "", "", ""], 1, kind="group", group="FIANCHI"),
        Row(["Fianco", "COB90", "3,1", "€ 97,00"], 1, group="FIANCHI"),
        Row(["Ripiano", "COBR90", "2,7", "56,50"], 2, group="RIPIANI"),
    ]
    table = Table(columns=cols, rows=rows, pages=[1, 2],
                  title="COBA - TABELLA PREZZI E COMPONENTI")
    cards = Table(
        columns=[Column("Prodotto", 0, 0), Column("Codice", 0, 0),
                 Column("Prezzo", 0, 0, kind="price")],
        rows=[Row(["JOLLY", "JOL", "52,00"], 3)],
        pages=[3], title="Schede prodotto", source="cards",
    )
    return ExtractionResult("source.pdf", [table, cards], 37,
                            meta={"configs": [
                                {"sezione": "SISTEMI", "prodotto": "COBA",
                                 "codice": "2xCOB90", "prezzo": "819,00",
                                 "pagina": 6, "note": "Codice Sistema"}
                            ]})


def test_export_xlsx(tmp_path: Path):
    result = sample_result()
    path = tmp_path / "out.xlsx"
    export_xlsx(result, path, AppSettings())
    wb = load_workbook(path)
    assert "COBA" in wb.sheetnames
    assert "Schede prodotto" in wb.sheetnames
    assert "Tutti i dati" in wb.sheetnames
    assert "Configurazioni" in wb.sheetnames
    ws = wb["COBA"]
    assert ws["A1"].value == "Descrizione"
    assert ws["A2"].value == "FIANCHI"
    # merged group row then data
    assert ws["D2"].value is None or ws["D2"].value == ""
    values = [ws.cell(row=r, column=4).value for r in range(1, ws.max_row + 1)]
    assert 97.0 in values
    assert ws.max_row == 4


def test_export_csv(tmp_path: Path):
    result = sample_result()
    path = tmp_path / "out.csv"
    export_csv(result, path, AppSettings())
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    assert lines[0].startswith("Tabella;Gruppo;Prodotto;Descrizione")
    assert "COBA;FIANCHI;;Fianco;COB90;3,1;97,00" in text
    assert "Schede prodotto;;JOLLY;;JOL;;52,00" in text
    rows = list(csv.reader(lines, delimiter=";"))
    assert len(rows) == 4


def test_export_configs_csv(tmp_path: Path):
    result = sample_result()
    path = tmp_path / "config.csv"
    out = export_configs_csv(result, path, AppSettings())
    assert out == path
    text = path.read_text(encoding="utf-8-sig")
    assert "Codice configurazione" in text
    assert "2xCOB90" in text
    assert "819,00" in text


def test_sheet_titles_do_not_collide(tmp_path: Path):
    table = Table(
        columns=[Column("Codice", 0, 0), Column("Prezzo", 0, 0, kind="price")],
        rows=[Row(["A1", "10,00"], 1)], pages=[1], title="Configurazioni",
    )
    result = ExtractionResult("source.pdf", [table], 1, meta={
        "configs": [{"sezione": "X", "prodotto": "Y", "codice": "A1",
                     "prezzo": "10,00", "pagina": 1}]
    })
    path = tmp_path / "c.xlsx"
    export_xlsx(result, path, AppSettings())
    wb = load_workbook(path)
    assert len(wb.sheetnames) == len(set(wb.sheetnames))
    assert "Configurazioni1" not in wb.sheetnames


def test_formula_injection_is_neutralised(tmp_path: Path):
    table = Table(
        columns=[Column("Descrizione", 0, 0), Column("Codice", 0, 0),
                 Column("Prezzo", 0, 0, kind="price")],
        rows=[Row(["=1+1", "-2+3", "10,00"], 1)], pages=[1], title="=1+1",
    )
    result = ExtractionResult("source.pdf", [table], 1, meta={"configs": [
        {"sezione": "@x", "prodotto": "P", "codice": "=1+1",
         "prezzo": "10,00", "pagina": 1, "note": "=cmd"}
    ]})
    xlsx = tmp_path / "i.xlsx"
    export_xlsx(result, xlsx, AppSettings())
    wb = load_workbook(xlsx)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                assert not (isinstance(cell.value, str) and cell.value.startswith("="))

    csv_path = tmp_path / "i.csv"
    export_csv(result, csv_path, AppSettings())
    rows = list(csv.reader(csv_path.read_text(encoding="utf-8-sig").splitlines(),
                           delimiter=";"))
    assert rows[1][0].startswith("'")
    assert rows[1][1].startswith("'")

    cfg_path = tmp_path / "i-config.csv"
    export_configs_csv(result, cfg_path, AppSettings())
    cfg = list(csv.reader(cfg_path.read_text(encoding="utf-8-sig").splitlines(),
                          delimiter=";"))
    assert cfg[1][0].startswith("'")
    assert cfg[1][2].startswith("'")

