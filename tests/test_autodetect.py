from pdf2list.core.autodetect import (
    FormatReport,
    _cluster_centers,
    _page_title,
    product_row_table,
    table_score,
    vertical_pair_table,
)
from pdf2list.core.models import PageText, Word
from pdf2list.core.normalize import canonical_header
from pdf2list.core.profiles import load_profile


def make_page(width=700, height=800, rows: list[list[tuple[str, float, float]]] | None = None):
    words: list[Word] = []
    for row in rows or []:
        for text, x, y in row:
            width_pt = max(8, len(text) * 5.0)
            words.append(Word(text, x, y, x + width_pt, y + 10))
    return PageText(number=1, width=width, height=height, words=words)


def test_header_aliases_cover_short_italian_labels():
    assert canonical_header("Descr.") == "Descrizione"
    assert canonical_header("descr") == "Descrizione"
    assert canonical_header("€") == "Prezzo"
    assert canonical_header("EUR") == "Prezzo"
    assert canonical_header("Euro") == "Prezzo"


def test_product_rows_with_two_price_columns():
    page = make_page(rows=[
        [("AB12", 40, 100), ("Base", 75, 100), ("test", 105, 100),
         ("10,00", 300, 100), ("12,00", 360, 100)],
        [("AB13", 40, 120), ("Base", 75, 120), ("large", 105, 120),
         ("20,00", 300, 120), ("22,00", 360, 120)],
    ])
    table = product_row_table(page, load_profile("generic"))
    assert table is not None
    assert [c.name for c in table.columns] == [
        "Prodotto", "Descrizione", "Codice", "Prezzo", "Prezzo 2",
    ]
    rows = table.data_rows()
    assert len(rows) == 2
    assert rows[0].cells[1:] == ["Base test", "AB12", "10,00", "12,00"]
    assert rows[1].cells[1:] == ["Base large", "AB13", "20,00", "22,00"]
    assert table_score([table]) > 0


def test_product_row_joins_price_split_by_stray_space():
    page = make_page(rows=[
        [("AB12", 40, 100), ("Base", 75, 100), ("test", 105, 100),
         ("5,", 300, 100), ("767", 310, 100)],
    ])
    table = product_row_table(page, load_profile("generic"))
    assert table is not None
    row = table.data_rows()[0]
    assert row.cells[3] == "5,767"


def test_product_row_accepts_code_alone_next_to_price_panel():
    page = make_page(rows=[
        [("AB12", 40, 100), ("2.246", 300, 100), ("2.470", 360, 100)],
    ])
    table = product_row_table(page, load_profile("generic"))
    assert table is not None
    row = table.data_rows()[0]
    assert row.cells[2] == "AB12"
    assert row.cells[3:] == ["2.246", "2.470"]


def test_vertical_pairing_rejects_prose_codes():
    page = make_page(rows=[
        [("Riserva di proprietà. Ai sensi dell'art. 1523 del Cod. Civile",
          40, 120), ("€ 9", 300, 120)],
        [("Cod. 19310NE02", 40, 200), ("EURO 146,00", 300, 200)],
    ])
    table = vertical_pair_table(page, load_profile("generic"))
    assert table is not None
    codes = [r.cells[2] for r in table.data_rows()]
    assert codes == ["19310NE02"]



    page = make_page(rows=[
        [("QV193DST27P", 40, 100), ("Base", 120, 100), ("piedini", 155, 100),
         ("497", 400, 100)],
        [("G1", 45, 120), ("Base", 75, 120), ("cromo", 105, 120),
         ("497", 400, 120)],
        [("B24", 45, 180), ("Base", 75, 180), ("legge", 105, 180),
         ("85", 400, 180)],
    ])
    table = product_row_table(page, load_profile("generic"))
    assert table is not None
    codes = [r.cells[2] for r in table.data_rows()]
    assert codes == ["QV193DST27P", "B24"]


def test_vertical_pairing_keeps_left_and_right_cards_apart():
    page = make_page(rows=[
        [("EURO 146,00", 40, 50), ("EURO 158,00", 520, 50)],
        [("Cod.", 50, 120), ("19310NE02", 90, 120),
         ("Cod.", 420, 120), ("19310CR01", 460, 120)],
    ])
    table = vertical_pair_table(page, load_profile("generic"))
    assert table is not None
    rows = {r.cells[2]: r.cells[3] for r in table.data_rows()}
    assert rows["19310NE02"] == "146,00"
    assert rows["19310CR01"] == "158,00"


def test_page_title_skips_dimension_lines():
    page = make_page(rows=[
        [("DEEP PLASTIC SOFT", 40, 40)],
        [("LARGHEZZA", 40, 180), ("67", 160, 180), ("PROFONDITA", 220, 180)],
    ])
    assert _page_title(page) == "DEEP PLASTIC SOFT"


def test_cluster_centers_groups_aligned_price_columns():
    centers = _cluster_centers([300.0, 300.4, 360.0, 359.8])
    assert len(centers) == 2
    assert abs(centers[0] - 300.2) < 0.1
    assert abs(centers[1] - 359.9) < 0.1


def test_format_report_summary_and_confidence():
    report = FormatReport()
    report.record(1, "rows", 1, 10)
    report.record(2, "rows", 1, 8)
    report.record(3, "none")
    assert report.kind == "rows"
    assert report.confidence == 0.67
    assert "Righe prodotto" in report.summary()
    assert "Tecniche" not in report.summary()
    meta = report.as_meta()
    assert meta["kind"] == "rows"
    assert meta["pages"]["1"] == "rows"
