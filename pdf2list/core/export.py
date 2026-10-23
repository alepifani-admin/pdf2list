"""Excel and CSV export with a clean, print-ready layout."""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .. import __app_name__, __version__
from .layout import _short_title
from .models import ExtractionResult, Table
from .normalize import (
    parse_number,
    safe_filename,
    sanitize_sheet_name,
    unique_sheet_names,
)
from .settings import AppSettings

log = logging.getLogger(__name__)

DARK = "2E3033"
GREY = "F2F3F5"
ACCENT = "D9DDE0"
WHITE = "FFFFFF"
BORDER = "BFC4C8"

PREFERRED = ["Sezione", "Prodotto", "Gruppo", "Descrizione", "Codice", "Struttura",
             "Componenti", "Pannelli", "Ganci", "Coperchio", "Ripiani", "Formato",
             "Elementi", "Misure", "Imballaggio", "Kg", "Prezzo", "Note"]

# Sheet names used by the exporter itself; a table title cannot reuse them.
FIXED_SHEETS = {"Indice", "Info", "Tutti i dati", "Configurazioni",
                "Avvisi", "Nessun dato", "Listino"}


@dataclass
class ExportResult:
    xlsx: Path | None = None
    csv: Path | None = None


def _fill(color: str) -> PatternFill:
    return PatternFill("solid", fgColor=color)


def _style_header(ws: Worksheet, row: int, ncols: int, height: int = 30) -> None:
    thin = Side(style="thin", color=BORDER)
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = _fill(DARK)
        cell.font = Font(bold=True, color=WHITE, size=10)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=thin)
    ws.row_dimensions[row].height = height


def _set_widths(ws: Worksheet, widths: list[int | float]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _auto_widths(ws: Worksheet, ncols: int, rows: int,
                 headers: list[str]) -> None:
    widths: list[float] = []
    for ci in range(1, ncols + 1):
        header = headers[ci - 1] if ci - 1 < len(headers) else ""
        longest = max(8, min(38, len(str(header)) + 2))
        for r in range(2, min(rows, 220) + 1):
            value = ws.cell(row=r, column=ci).value
            if value is None:
                continue
            text = str(value)
            longest = max(longest, min(48, len(text) + 2))
        widths.append(longest)
    _set_widths(ws, widths)


def _unique_headers(tables: list[Table]) -> list[str]:
    seen: list[str] = []
    for t in tables:
        for h in t.headers:
            if h not in seen:
                seen.append(h)
    return [h for h in PREFERRED if h in seen] + [h for h in seen if h not in PREFERRED]


def _sheet_title(t: Table, index: int) -> str:
    title = _short_title(t.title)
    if not title:
        title = "Listino" if index == 0 else f"Tabella {index + 1}"
    return sanitize_sheet_name(title, "Listino")


def _is_price(col_name: str, kind: str = "") -> bool:
    if kind == "price":
        return True
    key = col_name.lower()
    return any(x in key for x in ("prezzo", "price", "prix", "precio", "preis",
                                  "importo", "€", "eur"))


def _money_format(symbol: str) -> str:
    sym = "".join(c for c in (symbol or "") if c not in '"&').strip()[:3]
    return f'#,##0.00 "{sym}"' if sym else "#,##0.00"


def _protect_formula(text: str) -> str:
    """Neutralise spreadsheet formula injection in extracted text."""
    if not text:
        return text
    if text[0] in "\t\r":
        return "'" + text
    if text[0] in "=+-@" and len(text) > 1:
        return "'" + text
    return text


def _prepare_cell(value: str, kind: str, currency: str):
    """Return (value, number_format) for a cell."""
    if value is None or str(value).strip() == "":
        return None, None
    text = str(value).strip()
    if kind in {"price", "number"}:
        number = parse_number(text)
        if number is not None:
            if kind == "price":
                return number, _money_format(currency)
            return number, "0.0##"
    # avoid spreadsheet formula injection from extracted text
    return _protect_formula(text), None


def build_workbook(result: ExtractionResult, settings: AppSettings) -> Workbook:
    export = settings.export
    wb = Workbook()
    wb.remove(wb.active)

    tables = [t for t in result.tables if t.data_rows()]
    if not tables:
        ws = wb.create_sheet("Nessun dato")
        ws["A1"] = "Nessuna tabella riconosciuta nel PDF."
        ws["A1"].font = Font(bold=True, size=14, color=DARK)
        return wb

    multi = len(tables) > 1
    per_table = export.one_sheet_per_table
    titles = unique_sheet_names([_sheet_title(t, i) for i, t in enumerate(tables)])
    # Avoid collisions with the sheets created by the exporter.  Rename first
    # and de-duplicate again, otherwise a table titled "Configurazioni" would
    # be silently renamed by openpyxl to "Configurazioni1".
    reserved = FIXED_SHEETS
    reserved_lower = {name.lower() for name in reserved}
    renamed = [
        sanitize_sheet_name(f"{name} tabella") if name.lower() in reserved_lower
        else name
        for name in titles
    ]
    titles = unique_sheet_names(renamed)

    if per_table:
        table_sheets = list(zip(tables, titles))
    else:
        table_sheets = []
    for t, name in table_sheets:
        ws = wb.create_sheet(name)
        headers = list(t.headers)
        ws.append(headers)
        _style_header(ws, 1, len(headers))
        n_rows = 0
        group_fill = _fill("E9EBED")
        for row in t.rows:
            if row.kind == "group":
                group_label = row.cells[0] if row.cells else row.group
                line = [_protect_formula(group_label)] + [""] * (len(headers) - 1)
                ws.append(line)
                n_rows += 1
                r = ws.max_row
                ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(headers))
                for c in range(1, len(headers) + 1):
                    ws.cell(row=r, column=c).fill = group_fill
                ws.cell(row=r, column=1).font = Font(bold=True, size=10, color=DARK)
                continue
            line = []
            fmts: list[tuple[int, str]] = []
            for ci, col in enumerate(t.columns):
                raw = row.cells[ci] if ci < len(row.cells) else ""
                kind = col.kind
                if _is_price(col.name, kind) and kind != "number":
                    kind = "price"
                value, fmt = _prepare_cell(raw, kind, export.currency_symbol)
                line.append(value)
                if fmt:
                    fmts.append((ci + 1, fmt))
            ws.append(line)
            n_rows += 1
            r = ws.max_row
            for ci, fmt in fmts:
                ws.cell(row=r, column=ci).number_format = fmt
            for ci, col in enumerate(t.columns):
                cell = ws.cell(row=r, column=ci + 1)
                cell.alignment = Alignment(
                    vertical="top", wrap_text=len(str(cell.value or "")) > 32,
                    horizontal="right" if cell.number_format and "0.00" in cell.number_format
                    else "left",
                )
                if r % 2 == 0:
                    cell.fill = _fill(GREY)
            if export.include_page_column:
                ws.cell(row=r, column=len(headers) + 1, value=row.page)
        if export.include_page_column:
            # add the page header after the data (openpyxl needs a header cell)
            # the column was written without a header; fix it now
            ws.cell(row=1, column=len(headers) + 1, value="Pagina")
            _style_header(ws, 1, len(headers) + 1)
        if export.auto_filter and n_rows:
            last = len(headers) + (1 if export.include_page_column else 0)
            ws.auto_filter.ref = f"A1:{get_column_letter(last)}{ws.max_row}"
        if export.freeze_header:
            ws.freeze_panes = "A2"
        headers_all = headers + (["Pagina"] if export.include_page_column else [])
        _auto_widths(ws, len(headers_all), ws.max_row, headers_all)
        ws.sheet_view.showGridLines = False
        ws.sheet_view.zoomScale = 90
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.oddFooter.left.text = f"{__app_name__} - {Path(result.source).stem}"
        ws.oddFooter.left.size = 8
        ws.oddFooter.right.text = "Pag. &P di &N"
        ws.oddFooter.right.size = 8
        ws.print_title_rows = "1:1" if n_rows else None
        ws.sheet_properties.tabColor = DARK if name == "Listino" else "6E7377"

    # combined sheet with a union of the columns
    need_combined = export.combined_sheet or not per_table
    if need_combined and tables:
        union = _unique_headers(tables)
        sheet_name = "Tutti i dati" if (per_table or multi) else "Listino"
        ws = wb.create_sheet(sheet_name)
        group_needed = ("Gruppo" not in union and any(
            r.group for t in tables for r in t.data_rows()))
        include_table = multi
        headers = ((["Tabella"] if include_table else [])
                   + (["Gruppo"] if group_needed else []) + union
                   + (["Pagina"] if export.include_page_column else []))
        ws.append(headers)
        _style_header(ws, 1, len(headers))
        for t in tables:
            for row in t.data_rows():
                values = []
                for h in union:
                    try:
                        ci = t.headers.index(h)
                    except ValueError:
                        values.append("")
                        continue
                    values.append(row.cells[ci] if ci < len(row.cells) else "")
                line = ([_protect_formula(_short_title(t.title) or "Listino")]
                        if include_table else [])
                if group_needed:
                    line.append(_protect_formula(row.group))
                line.extend(values)
                if export.include_page_column:
                    line.append(row.page)
                ws.append(line)
                r = ws.max_row
                offset = 1 + (1 if include_table else 0) + (1 if group_needed else 0)
                for ci, h in enumerate(union, start=offset):
                    raw = line[ci - 1] if ci - 1 < len(line) else ""
                    value, fmt = _prepare_cell(
                        raw,
                        "price" if _is_price(h) else
                        ("number" if h in {"Kg", "Quantità", "Qty"} else "text"),
                        export.currency_symbol,
                    )
                    ws.cell(row=r, column=ci, value=value)
                    if fmt:
                        ws.cell(row=r, column=ci).number_format = fmt
                for c in range(1, len(headers) + 1):
                    ws.cell(row=r, column=c).alignment = Alignment(
                        vertical="top", wrap_text=c in (2, 3, 4))
                    if r % 2 == 0:
                        ws.cell(row=r, column=c).fill = _fill(GREY)
        if export.auto_filter:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"
        if export.freeze_header:
            ws.freeze_panes = "B2" if include_table else "A2"
        _auto_widths(ws, len(headers), ws.max_row, headers)
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.sheet_properties.tabColor = DARK

    # configurations extracted from the black info badges
    configs = result.meta.get("configs") or []
    if configs:
        ws = wb.create_sheet("Configurazioni")
        headers = ["Sezione", "Prodotto", "Codice configurazione", "Prezzo €",
                   "Pagina PDF", "Dettaglio"]
        ws.append(headers)
        _style_header(ws, 1, len(headers))
        for c in configs:
            ws.append([
                _protect_formula(c.get("sezione", "")),
                _protect_formula(c.get("prodotto", "")),
                _protect_formula(c.get("codice", "")),
                parse_number(c.get("prezzo")) if c.get("prezzo") else None,
                c.get("pagina", ""),
                _protect_formula(c.get("note", "")),
            ])
        for r in range(2, ws.max_row + 1):
            ws.cell(row=r, column=4).number_format = _money_format(export.currency_symbol)
            for c in range(1, len(headers) + 1):
                ws.cell(row=r, column=c).alignment = Alignment(
                    vertical="top", wrap_text=c in (2, 3, 6))
                if r % 2 == 0:
                    ws.cell(row=r, column=c).fill = _fill(GREY)
        _set_widths(ws, [16, 24, 38, 14, 10, 70])
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:F{ws.max_row}"
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = "6E7377"
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True

    # index
    if multi and per_table:
        ws = wb.create_sheet("Indice", 0)
        headers = ["Tabella", "Foglio", "Righe", "Pagine", "Origine", "Note"]
        ws.append(headers)
        _style_header(ws, 1, len(headers))
        for t, name in zip(tables, titles):
            pages = ", ".join(str(p) for p in sorted(set(t.pages))[:12])
            warnings = " ".join(t.warnings)
            ws.append([_protect_formula(t.title or name), name, len(t.data_rows()), pages,
                       _protect_formula(t.source), _protect_formula(warnings)])
        if configs:
            pages = ", ".join(str(p) for p in sorted({c.get("pagina") for c in configs})[:12])
            ws.append([_protect_formula("Configurazioni dai badge"), "Configurazioni", len(configs),
                       pages, "badge", "Codici e prezzi delle configurazioni complete"])
        for r in range(2, ws.max_row + 1):
            for c in range(1, len(headers) + 1):
                ws.cell(row=r, column=c).alignment = Alignment(vertical="top", wrap_text=True)
        _set_widths(ws, [34, 24, 8, 16, 12, 60])
        ws.freeze_panes = "A2"
        ws.sheet_view.showGridLines = False

    # info
    ws = wb.create_sheet("Info")
    ocr_pages = result.meta.get("ocr_pages") or []
    info = [
        (f"{__app_name__} {__version__}", ""),
        ("", ""),
        ("File di origine", Path(result.source).name),
        ("Pagine PDF", str(result.pages)),
        ("Tabelle estratte", str(len(tables))),
        ("Righe totali", str(result.row_count)),
        ("Configurazioni", str(len(result.meta.get("configs") or []))),
        ("Profilo", settings.profile_id),
        ("Data", datetime.now().strftime("%d/%m/%Y %H:%M")),
        ("OCR", (f"usato ({len(ocr_pages)} pagine)" if ocr_pages else "non usato")),
        ("AI", "usata (verifica i dati)" if result.meta.get("ai_used") else "non usata"),
        ("", ""),
        ("Nota", ("I dati sono estratti automaticamente dal PDF: verifica sempre "
                  "prezzi, codici e unità di misura prima dell'uso commerciale.")),
    ]
    for a, b in info:
        ws.append([a, b])
    ws["A1"].font = Font(bold=True, size=14, color=DARK)
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 100
    for r in range(1, ws.max_row + 1):
        ws.cell(row=r, column=2).alignment = Alignment(wrap_text=True, vertical="top")
    ws.sheet_view.showGridLines = False

    # warnings
    if result.warnings or any(t.warnings for t in tables):
        ws = wb.create_sheet("Avvisi")
        ws.append(["Avvisi"])
        ws["A1"].font = Font(bold=True, size=12, color=DARK)
        for w in list(result.warnings) + [w for t in tables for w in t.warnings]:
            if w:
                ws.append([_protect_formula(w)])
        ws.column_dimensions["A"].width = 120
        for r in range(2, ws.max_row + 1):
            ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True, vertical="top")

    wb.active = 0
    return wb


def export_xlsx(result: ExtractionResult, path: str | Path,
                settings: AppSettings) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = build_workbook(result, settings)
    wb.save(path)
    return path


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def _csv_value(value: str, kind: str, export) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if kind in {"price", "number"}:
        num = parse_number(text)
        if num is not None:
            if kind == "price":
                out = f"{num:.2f}"
            else:
                out = f"{num:.3f}".rstrip("0").rstrip(".")
            return out.replace(".", ",") if export.decimal_comma else out
    return _protect_formula(text)


def csv_columns(tables: list[Table], include_page: bool,
                group_needed: bool = False) -> list[str]:
    union = _unique_headers(tables)
    return ((["Tabella"] if len(tables) > 1 else [])
            + (["Gruppo"] if group_needed else [])
            + union + (["Pagina"] if include_page else []))


def export_csv(result: ExtractionResult, path: str | Path,
               settings: AppSettings) -> Path:
    export = settings.export
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tables = [t for t in result.tables if t.data_rows()]
    union = _unique_headers(tables)
    group_needed = ("Gruppo" not in union and any(
        r.group for t in tables for r in t.data_rows()))
    headers = csv_columns(tables, export.include_page_column, group_needed)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=export.csv_delimiter or ";",
                            quoting=csv.QUOTE_MINIMAL)
        writer.writerow(headers)
        for t in tables:
            for row in t.data_rows():
                cells = []
                if len(tables) > 1:
                    cells.append(_protect_formula(_short_title(t.title) or "Listino"))
                if group_needed:
                    cells.append(_protect_formula(row.group))
                for h in union:
                    try:
                        ci = t.headers.index(h)
                    except ValueError:
                        cells.append("")
                        continue
                    raw = row.cells[ci] if ci < len(row.cells) else ""
                    kind = "price" if _is_price(h) else (
                        "number" if h in {"Kg", "Quantità", "Qty"} else "text")
                    cells.append(_csv_value(raw, kind, export))
                if export.include_page_column:
                    cells.append(str(row.page))
                writer.writerow(cells)
    return path


def export_configs_csv(result: ExtractionResult, path: str | Path,
                       settings: AppSettings) -> Path | None:
    configs = result.meta.get("configs") or []
    if not configs:
        return None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = ["Sezione", "Prodotto", "Codice configurazione", "Prezzo EUR",
               "Pagina PDF", "Dettaglio"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=settings.export.csv_delimiter or ";",
                            quoting=csv.QUOTE_MINIMAL)
        writer.writerow(headers)
        for c in configs:
            price = c.get("prezzo", "")
            if price:
                num = parse_number(price)
                if num is not None:
                    price = f"{num:.2f}".replace(
                        ".", "," if settings.export.decimal_comma else ".")
            writer.writerow([_protect_formula(c.get("sezione", "")),
                             _protect_formula(c.get("prodotto", "")),
                             _protect_formula(c.get("codice", "")), price,
                             c.get("pagina", ""), _protect_formula(c.get("note", ""))])
    return path


def default_output_paths(source: str | Path, out_dir: str | Path | None = None) -> tuple[Path, Path]:
    src = Path(source)
    base = safe_filename(src.stem, "listino")
    folder = Path(out_dir) if out_dir else src.parent
    return folder / f"{base}.xlsx", folder / f"{base}.csv"


def configs_path_for(csv_path: str | Path) -> Path:
    p = Path(csv_path)
    return p.with_name(f"{p.stem} - CONFIGURAZIONI.csv")
