"""High-level extraction pipeline."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .layout import fallback_tables, extract_tables, table_from_matrix
from .models import ExtractionResult, Table
from .pdftext import OcrError, PdfDocument, PdfError, PdfPasswordRequired
from .profiles import Profile

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]
CancelFn = Callable[[], bool]


@dataclass
class ExtractOptions:
    ocr_mode: str = "auto"  # auto | always | never
    dpi: int = 200
    first_page: int = 1
    last_page: int | None = None
    profile_id: str = "generic"
    profile_path: str | None = None
    password: str | None = None
    use_ruled_tables: bool = True
    use_fallback: bool = True


class ExtractionCancelled(RuntimeError):
    pass


def extract_pdf(
    path: str | Path,
    options: ExtractOptions,
    profile: Profile | None = None,
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> ExtractionResult:
    """Extract all price tables from a PDF."""
    if profile is None:
        from .profiles import load_profile

        profile = load_profile(options.profile_id, options.profile_path)
    result = ExtractionResult(source=str(path))
    total_hint = 1
    with PdfDocument(path, password=options.password) as doc:
        result.pages = doc.page_count
        first = max(1, int(options.first_page or 1))
        last = min(doc.page_count, int(options.last_page or doc.page_count))
        if first > last:
            raise PdfError("Intervallo di pagine non valido.")
        total = last - first + 1
        total_hint = total
        empty_pages: list[int] = []
        for n, idx in enumerate(range(first - 1, last)):
            if cancel and cancel():
                raise ExtractionCancelled("Operazione annullata.")
            pno = idx + 1
            if progress:
                progress(f"Analisi pagina {pno} di {last}", n, total)

            def page_progress(message: str, _cur: int, _tot: int) -> None:
                if progress:
                    progress(message, n, total)

            try:
                page = doc.extract_page(idx, ocr_mode=options.ocr_mode,
                                        dpi=options.dpi, progress=page_progress)
            except OcrError as exc:
                result.warnings.append(f"Pagina {pno}: {exc}")
                empty_pages.append(pno)
                continue
            except PdfError:
                raise
            except Exception as exc:
                log.exception("page %s failed", pno)
                result.warnings.append(f"Pagina {pno}: errore di lettura ({exc}).")
                empty_pages.append(pno)
                continue
            if not page.words:
                empty_pages.append(pno)
                continue
            tables = extract_tables(page, profile)
            if not tables and options.use_ruled_tables:
                tables = _ruled_tables(doc, idx, pno, profile)
            if not tables and options.use_fallback:
                tables = fallback_tables(page, profile)
            if tables:
                tables = _filter_tables(tables)
            if not tables:
                empty_pages.append(pno)
                continue
            for t in tables:
                if not t.title:
                    t.title = _page_label(page)
                result.tables.append(t)
            configs = _collect_configs(page, tables, profile, pno)
            if configs:
                result.meta.setdefault("configs", []).extend(configs)
            if progress:
                progress(f"Pagina {pno}: {sum(len(t.data_rows()) for t in tables)} righe",
                         n + 1, total)
        if not result.tables:
            hint = "Nessuna tabella riconosciuta."
            if empty_pages:
                hint += " Pagine senza dati: " + ", ".join(str(p) for p in empty_pages[:12])
                if len(empty_pages) > 12:
                    hint += "…"
            hint += (" Prova a forzare l'OCR o a usare un profilo più adatto."
                     if options.ocr_mode != "always" else
                     " Prova ad aumentare la risoluzione OCR.")
            result.warnings.append(hint)
        if empty_pages:
            result.meta["empty_pages"] = empty_pages
        result.merge_similar()
        _apply_profile_overrides(result, profile)
    if progress:
        progress("Completato", total_hint, total_hint)
    return result


def _collect_configs(page, tables: list[Table], profile: Profile,
                     pno: int) -> list[dict]:
    from .normalize import clean_text, is_badge_text, is_numeric_token
    from .layout import _short_title

    all_lines = page.lines()
    badge_lines = [ln for ln in all_lines if is_badge_text(ln.text, profile)]
    if not badge_lines:
        return []

    def x_overlap(a, b) -> float:
        return min(a.x1, b.x1) - max(a.x0, b.x0)

    blocks: list[list] = []
    used: set[int] = set()
    for anchor in badge_lines:
        if id(anchor) in used:
            continue
        block = [anchor]
        for ln in all_lines:
            if ln is anchor:
                continue
            if abs(ln.y - anchor.y) > max(18.0, anchor.height * 3.2):
                continue
            if x_overlap(ln, anchor) <= min(ln.x1 - ln.x0, anchor.x1 - anchor.x0) * 0.25:
                continue
            if is_badge_text(ln.text, profile) or not ln.text.strip():
                block.append(ln)
            elif (len(clean_text(ln.text)) <= 120
                  and not is_numeric_token(ln.text)
                  and not is_badge_text(ln.text, profile)
                  and abs(ln.y - anchor.y) <= max(12.0, anchor.height * 2.0)):
                # continuation of a multi-line badge code
                block.append(ln)
        block.sort(key=lambda l: (l.y, l.x0))
        blocks.append(block)
        for ln in block:
            used.add(id(ln))
        if len(block) > 1:
            used.add(id(anchor))

    section = ""
    for ln in all_lines:
        if page.height * 0.03 <= ln.y <= page.height * 0.13:
            text = clean_text(ln.text)
            if text and text.upper() == text and any(c.isalpha() for c in text) and len(text) < 50:
                section = profile.apply_section_fix(text)
                break
    is_cards = any(t.source == "cards" for t in tables)
    table_product = _short_title(tables[0].title) if tables else _page_label(page)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        anchor = block[0]
        product = table_product
        if is_cards:
            for ln in sorted(all_lines, key=lambda l: -(l.y)):
                if ln.y >= anchor.y:
                    continue
                if anchor.y - ln.y > 420:
                    break
                if x_overlap(ln, anchor) <= 0:
                    continue
                text = clean_text(ln.text)
                if not text or is_badge_text(text, profile) or len(text.split()) > 4:
                    continue
                if len(text) > 40 or is_numeric_token(text):
                    continue
                if not text[0].isupper():
                    continue
                if re.match(r"^(versione|disponibile|appendiabiti|struttura|componenti|"
                            r"ppl|in |con |per |adatto|fornito|colore|finitura)\b",
                            text, re.I):
                    continue
                if re.search(r"(\d\s*[×xX]\s*\d|\bcm\b|\bmm\b|\bkg\b|€|[|,])", text, re.I):
                    continue
                first = text.split()[0]
                from .layout import _card_field
                if _card_field(first, profile) is not None:
                    continue
                product = text
                break
        text = " ".join(clean_text(ln.text) for ln in block)
        note = " | ".join(clean_text(ln.text) for ln in block)
        code = ""
        price = None
        m = re.search(r"visualizz\w*\s*:?\s*(.+?)\s*(?:\|\s*)?Prezzo", text, re.IGNORECASE)
        if m:
            code = m.group(1).strip(" |:;,.")
        else:
            m = re.search(r"visualizz\w*\s*:?\s*([A-Za-z0-9+./×xX_-]+)", text, re.IGNORECASE)
            if m:
                code = m.group(1).strip(" |:;,.")
        m = re.search(r"Prezzo[^\d€]{0,30}€?\s*([\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)",
                      text, re.IGNORECASE)
        if m:
            from .normalize import parse_number

            price = parse_number(m.group(1))
        if not code and price is None:
            continue
        if not code:
            # a price without a configuration code is not actionable
            continue
        key = (code.lower(), "" if price is None else f"{price:.2f}")
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(sezione=section, prodotto=product, codice=code,
                        prezzo="" if price is None else f"{price:.2f}".replace(".", ","),
                        pagina=pno, note=note))
    return out


def _filter_tables(tables: list[Table]) -> list[Table]:
    """Apply strict quality rules to heuristic/ruled fallbacks, not to cards."""
    from .layout import _table_quality

    out: list[Table] = []
    for t in tables:
        if t.source == "cards":
            out.append(t)
            continue
        if t.source in {"rules", "fallback"} and not _table_quality(t):
            continue
        out.append(t)
    return out


def _apply_profile_overrides(result: ExtractionResult, profile: Profile) -> None:
    if not (profile.description_overrides or profile.product_code_overrides):
        return
    from .layout import _short_title

    for table in result.tables:
        headers = table.headers
        code_idx = _index_of(headers, "Codice")
        desc_idx = _index_of(headers, "Descrizione")
        if code_idx is None or desc_idx is None:
            continue
        product = _short_title(table.title)
        for row in table.data_rows():
            if code_idx >= len(row.cells) or desc_idx >= len(row.cells):
                continue
            code = (row.cells[code_idx] or "").strip()
            desc = row.cells[desc_idx] or ""
            fixed = profile.apply_description_overrides(product, code, desc)
            if fixed != desc:
                row.cells[desc_idx] = fixed


def _index_of(headers: list[str], name: str) -> int | None:
    for i, h in enumerate(headers):
        if h == name:
            return i
    return None


def _ruled_tables(doc: PdfDocument, index: int, pno: int,
                  profile: Profile) -> list[Table]:
    """Use vector ruling lines when the header heuristic found nothing."""
    out: list[Table] = []
    try:
        finder = doc.doc[index].find_tables()
        tabs = list(getattr(finder, "tables", []) or [])
    except Exception:
        return out
    for tab in tabs:
        try:
            matrix = tab.extract()
        except Exception:
            continue
        if not matrix:
            continue
        title = _title_above(doc, index, getattr(tab, "bbox", None))
        table = table_from_matrix(matrix, pno, profile, title=title)
        if table:
            table.source = "rules"
            out.append(table)
    return out


def _title_above(doc: PdfDocument, index: int, bbox) -> str:
    if not bbox:
        return ""
    try:
        page = doc.doc[index]
        y_limit = float(bbox[1])
        best = None
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                text = "".join(span.get("text", "") for span in line.get("spans", []))
                y = float(line.get("bbox", [0, 0, 0, 0])[1])
                if y >= y_limit:
                    continue
                if y_limit - y > 150:
                    continue
                size = max((float(s.get("size", 0)) for s in line.get("spans", [])),
                           default=0)
                if text.strip() and (best is None or size > best[0]):
                    best = (size, text.strip())
        return best[1] if best else ""
    except Exception:
        return ""


def _page_label(page) -> str:
    """A light title guess from the largest line in the top third."""
    try:
        lines = page.lines()
    except Exception:
        return ""
    if not lines:
        return ""
    top = [ln for ln in lines if ln.y < page.height * 0.34]
    if not top:
        return ""
    best = max(top, key=lambda l: l.height)
    if best.height < 12:
        return ""
    text = best.text.strip()
    if len(text) > 80 or len(text.split()) > 12:
        return ""
    return text
