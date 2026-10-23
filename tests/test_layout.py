from pdf2list.core.layout import extract_tables
from pdf2list.core.models import PageText, Word
from pdf2list.core.profiles import load_profile


def make_page() -> PageText:
    words: list[Word] = []

    def add(text, x, y, w=None, h=10):
        w = w if w is not None else max(8, len(text) * 5.2)
        words.append(Word(text, x, y, x + w, y + h))

    # header
    for t, x in [("Descrizione", 40), ("Codice", 300), ("Misure", 430),
                 ("Kg", 520), ("Prezzo", 580)]:
        add(t, x, 100)
    # group row
    add("COMPONENTE / PANCA", 42, 120, 160)
    # data row 1 (description wraps onto the next line)
    add("Panca", 42, 140)
    add("New", 75, 140)
    add("Slim", 100, 140)
    add("NSLI", 300, 140)
    add("110x62x83", 430, 140, 60)
    add("20", 525, 140, 12)
    add("344,00", 580, 140, 30)
    add("a", 42, 152)
    add("2", 50, 152)
    add("posti", 58, 152)
    # data row 2
    add("Panca New Slim a 3 posti", 42, 165, 150)
    add("NSLI", 300, 165)
    add("165×62×83", 430, 165, 60)
    add("26,5", 522, 165, 18)
    add("467,00", 580, 165, 30)
    return PageText(number=1, width=650, height=800, words=words)


def test_extract_synthetic_table():
    page = make_page()
    tables = extract_tables(page, load_profile("generic"))
    assert len(tables) == 1
    t = tables[0]
    assert [c.name for c in t.columns] == [
        "Descrizione", "Codice", "Misure", "Kg", "Prezzo"
    ]
    data = t.data_rows()
    assert len(data) == 2
    assert data[0].cells[0] == "Panca New Slim a 2 posti"
    assert data[0].cells[1] == "NSLI"
    assert data[0].cells[3] == "20"
    assert data[0].cells[4] == "344,00"
    assert data[1].cells[0] == "Panca New Slim a 3 posti"
    assert data[1].cells[2] == "165×62×83"
    assert t.rows[0].kind == "group"
    assert data[0].group == "COMPONENTE / PANCA"
