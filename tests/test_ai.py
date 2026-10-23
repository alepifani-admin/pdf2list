import json

from pdf2list.core.ai import (
    AISettings,
    AIClient,
    _extract_json,
    clean_table_rows,
    map_table_headers,
)
from pdf2list.core.models import Column, Row, Table


def test_extract_json_from_fences():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('Ecco: [{"x": 2}]') == [{"x": 2}]


class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def chat(self, *args, **kwargs):
        return json.dumps(self.payload)


def sample_table() -> Table:
    return Table(
        columns=[Column("Cod.", 0, 0), Column("Price", 0, 0),
                 Column("Descrizione", 0, 0)],
        rows=[
            Row(["A1", "10,00", "Panca"], 1),
            Row(["A2", "20,00", "Tavolo"], 1),
        ],
        pages=[1], title="Test",
    )


def test_map_headers():
    table = sample_table()
    client = FakeClient({"mapping": {"Cod.": "Codice", "Price": "prezzo"}})
    map_table_headers(client, table)
    assert table.headers == ["Codice", "Prezzo", "Descrizione"]


def test_clean_rows_keeps_numbers():
    table = sample_table()
    client = FakeClient({"rows": [["A1", "10,00", "Panca"], ["A2", "99,00", "Tavolo"]]})
    clean_table_rows(client, table)
    assert table.rows[0].cells == ["A1", "10,00", "Panca"]
    # a changed number is rejected and the original kept
    assert table.rows[1].cells[1] == "20,00"
    assert table.rows[1].cells[2] == "Tavolo"
