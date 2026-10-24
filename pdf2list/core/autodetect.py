"""Adaptive format detection.

Price lists rarely share the same layout.  The classic header heuristic works
well for real tables, but many catalogues use "product rows" (a code, a
description and one or more prices on the same visual line), product cards
(code and price in separate blocks) or ruled tables whose header is repeated
above every group of rows.

This module inspects a page, tries the extraction strategies that fit its
layout and returns the best table together with a short description of the
detected format.  It is intentionally independent from the GUI and from the
AI layer so it can be unit-tested with synthetic pages.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from statistics import median

from .models import Column, PageText, Row, Table, TextLine, Word
from .normalize import canonical_header, clean_text, column_kind, parse_number
from .profiles import Profile

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

CURRENCY_RE = re.compile(r"(?:€|\bEUR(?:O)?\b)", re.IGNORECASE)
CURRENCY_PRICE_RE = re.compile(
    r"(?:€|\bEUR(?:O)?\b)\s*(\d[\d.,]*)|(\d[\d.,]*)\s*(?:€|\bEUR(?:O)?\b)",
    re.IGNORECASE,
)
CODE_LABEL_RE = re.compile(
    r"\b(?:cod(?:ice)?|code|art(?:icolo)?|ref(?:erence)?)\b[.:]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9._/+%-]{1,24})",
    re.IGNORECASE,
)
NUMERIC_RE = re.compile(r"^\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?$|^\d+$")
UNIT_WORDS = {
    "kg", "kgs", "g", "gr", "mc", "m3", "m³", "cm", "mm", "mt", "m", "h",
    "h.", "pz", "pcs", "n", "n.", "nr", "lt", "l", "%", "anni", "years",
    "d.", "ø", "Ø", "x", "×", "max", "min",
}
SPEC_RE = re.compile(
    r"^(?:peso|weight|volume|volume\.?|mc\.?|m3|kg\.?|cm\.?|mm\.?|dimension|"
    r"misure|altezza|larghezza|profondità|profondita|height|width|depth|"
    r"en\d|l'en\d|ordine|minimum|min\.|max\.|codice|code|euro|eur|€|"
    r"prezzo|price|iva|pag\.|pagina|tel\.|fax|www\.)",
    re.IGNORECASE,
)
BAD_CODES = {
    "EN16139", "EN1335", "EN1335A", "EN12520", "EN1022", "EN13761",
    "M8", "M6", "M10", "3000K", "4000K", "2700K", "6500K", "IP20", "IP44",
}

TECHNIQUE_LABELS = {
    "header": "intestazioni di colonna",
    "rules": "tabelle bordate",
    "fallback": "riconoscimento numerico",
    "cards": "schede prodotto",
    "rows": "righe prodotto",
    "vertical": "schede codice/prezzo",
    "mixed": "più tecniche",
    "none": "nessuna",
}
FORMAT_LABELS = {
    "header": "Tabella con intestazioni",
    "rules": "Tabella bordata",
    "fallback": "Tabella numerica",
    "cards": "Schede prodotto a campi",
    "rows": "Righe prodotto (codice + prezzo)",
    "vertical": "Schede prodotto (codice e prezzo)",
    "mixed": "Formato misto",
    "unknown": "Formato non riconosciuto",
}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class FormatReport:
    """Collects what the adaptive engine saw page by page."""

    pages: dict[int, str] = field(default_factory=dict)
    techniques: Counter = field(default_factory=Counter)
    rows: int = 0
    tables: int = 0
    notes: list[str] = field(default_factory=list)

    def record(self, page: int, technique: str, tables: int = 0, rows: int = 0) -> None:
        technique = technique or "none"
        self.pages[page] = technique
        self.techniques[technique] += 1
        self.tables += tables
        self.rows += rows

    @property
    def kind(self) -> str:
        useful = {k: v for k, v in self.techniques.items() if k != "none"}
        if not useful:
            return "unknown"
        if len(useful) > 1:
            return "mixed"
        return next(iter(useful))

    @property
    def label(self) -> str:
        return FORMAT_LABELS.get(self.kind, FORMAT_LABELS["unknown"])

    @property
    def confidence(self) -> float:
        total = sum(self.techniques.values())
        if not total:
            return 0.0
        best = max((v for k, v in self.techniques.items() if k != "none"), default=0)
        return round(min(1.0, best / total), 2)

    def summary(self) -> str:
        if self.kind == "unknown":
            return "Formato non riconosciuto: nessuna struttura di prezzi trovata."
        seen = ", ".join(
            f"{TECHNIQUE_LABELS.get(k, k)} ({v} pag.)"
            for k, v in self.techniques.most_common() if k != "none"
        )
        total = sum(self.techniques.values())
        return (f"{self.label} · {self.tables} tabelle, {self.rows} righe · "
                f"pagine analizzate: {total} ({seen})")

    def as_meta(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "confidence": self.confidence,
            "tables": self.tables,
            "rows": self.rows,
            "techniques": dict(self.techniques),
            "pages": {str(k): v for k, v in sorted(self.pages.items())},
            "summary": self.summary(),
        }


# ---------------------------------------------------------------------------
# Small geometry helpers
# ---------------------------------------------------------------------------

def _glue_numeric_words(words: list[Word]) -> list[Word]:
    """Re-join prices split by a stray space (``5, 767`` -> ``5,767``)."""
    out: list[Word] = []
    index = 0
    while index < len(words):
        word = words[index]
        if index + 1 < len(words):
            nxt = words[index + 1]
            head = word.text.strip()
            tail = nxt.text.strip()
            if (re.fullmatch(r"\d{1,3}[.,]", head)
                    and re.fullmatch(r"\d{3}", tail)
                    and 0 <= nxt.x0 - word.x1 <= 8):
                out.append(Word(head + tail, word.x0, word.y0, nxt.x1, nxt.y1,
                                word.conf, word.line_id))
                index += 2
                continue
        out.append(word)
        index += 1
    return out


def _visual_rows(page: PageText) -> list[list[TextLine]]:
    """Group TextLines that share a visual baseline."""
    lines = sorted(page.lines(), key=lambda ln: (ln.y, ln.x0))
    rows: list[list[TextLine]] = []
    for ln in lines:
        if rows:
            ref = rows[-1][0]
            tol = max(3.0, min(ln.height or 1.0, ref.height or 1.0) * 0.6)
            if abs(ln.y - ref.y) <= tol:
                rows[-1].append(ln)
                continue
        rows.append([ln])
    return rows


def _segment_words(words: list[Word], page: PageText) -> list[list[Word]]:
    """Split a visual row on wide horizontal corridors (side-by-side panels)."""
    words = sorted(words, key=lambda w: w.x0)
    segments: list[list[Word]] = []
    gap_limit = max(24.0, page.width * 0.055)
    for word in words:
        if segments and word.x0 - segments[-1][-1].x1 <= gap_limit:
            segments[-1].append(word)
        else:
            segments.append([word])
    return segments


def _strip_token(token: str) -> str:
    return token.strip(" \t\r\n,;:()[]{}")


def _is_code_token(token: str, *, first: bool) -> bool:
    raw = _strip_token(token)
    if not raw or raw != token.strip(" \t\r\n,;:"):
        # parentheses/brackets usually contain finishes or notes, not codes
        return False
    if not 3 <= len(raw) <= 24:
        return False
    if raw.upper() in BAD_CODES:
        return False
    if not re.search(r"[A-Za-z]", raw) or not re.search(r"\d", raw):
        return False
    if re.fullmatch(r"(?:EN|UNI|ISO|IEC|DIN)\s*\d+[A-Za-z0-9.-]*", raw, re.IGNORECASE):
        return False
    if re.fullmatch(r"\d+[KkMmGg]", raw):
        return False
    if re.fullmatch(r"\d{4,}[A-Za-z]?", raw):
        return False
    if not first and not raw[:1].isupper():
        return False
    return True


def _segment_has_code(segment: list[Word]) -> bool:
    if _explicit_code(" ".join(w.text for w in segment)):
        return True
    return any(
        _is_code_token(word.text, first=(i == 0))
        for i, word in enumerate(segment[:2])
    )


def _explicit_code(text: str) -> str | None:
    match = CODE_LABEL_RE.search(clean_text(text))
    if not match:
        return None
    code = match.group(1).strip(".,;:()[]")
    if not code or not re.search(r"\d", code):
        return None
    return code


def _labeled_code(text: str) -> str | None:
    """A code label at the start of a short product line (not prose)."""
    clean = clean_text(text)
    code = _explicit_code(clean)
    if not code:
        return None
    match = CODE_LABEL_RE.search(clean)
    if not match:
        return None
    label = match.group(0).split()[0].strip(".:").lower()
    if label in {"art", "artt", "articolo", "articoli"}:
        return None
    if match.start() > 24:
        return None
    if len(clean.split()) > 8 or len(clean) > 90:
        return None
    return code


def _price_from_token(token: str, previous: list[str]) -> tuple[str, float] | None:
    """Return (raw, value) when the token is a plausible price."""
    raw = _strip_token(token)
    if not raw:
        return None
    labeled = bool(CURRENCY_RE.search(raw))
    value: float | None = None
    if labeled:
        digits = re.search(r"\d[\d.,]*", raw)
        if not digits:
            return None
        value = parse_number(digits.group(0).rstrip(".,"))
        if value is None or value < 1:
            return None
        return raw, value
    if not NUMERIC_RE.fullmatch(raw):
        return None
    for prev in previous[-2:]:
        if prev.strip(".,:;()").lower() in UNIT_WORDS:
            return None
    value = parse_number(raw)
    if value is None or value < 5:
        return None
    # bare years are not prices (but "20,00" is a price, not the year 2000)
    if re.fullmatch(r"(?:19|20)\d{2}", raw):
        return None
    return raw, value


def _trailing_prices(segment: list[Word]) -> list[tuple[int, str, float]]:
    """Prices at the end of a segment (e.g. "B25 Base ... 89")."""
    out: list[tuple[int, str, float]] = []
    for index in range(len(segment) - 1, -1, -1):
        word = segment[index]
        parsed = _price_from_token(word.text, [w.text for w in segment[:index]])
        if parsed is None:
            break
        out.append((index, parsed[0], parsed[1]))
    out.reverse()
    return out


def _pure_price_segment(segment: list[Word]) -> list[tuple[int, str, float, Word]]:
    """A segment made only of prices/markers (right-hand price panel)."""
    out: list[tuple[int, str, float, Word]] = []
    for index, word in enumerate(segment):
        cleaned = _strip_token(word.text)
        if not cleaned or cleaned in {"*", "**", "-", "–", "€"}:
            continue
        parsed = _price_from_token(word.text, [w.text for w in segment[:index]])
        if parsed is None:
            return []
        out.append((index, parsed[0], parsed[1], word))
    return out


def _marker_segment(segment: list[Word]) -> bool:
    return all(
        _strip_token(word.text) in {"", "*", "**", "-", "–", "—", "€"}
        for word in segment
    )


def _cluster_centers(values: list[float], tolerance: float = 7.0) -> list[float]:
    groups: list[list[float]] = []
    for value in sorted(values):
        if groups and abs(value - groups[-1][-1]) <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(g) / len(g) for g in groups]


HEADER_TOKENS = {
    "cod", "cod.", "codice", "code", "descr", "descr.", "descrizione",
    "description", "prezzo", "price", "l", "h", "sp", "kg", "€", "eur",
    "cat", "cat.", "cat.0", "cat.a", "cat.b", "stamp", "stamp.", "wall",
    "dimensions", "dimensioni", "misure",
}


def _is_header_like(text: str) -> bool:
    """Table header rows (``Cod. Descr. L H Sp Kg €``) are not page titles."""
    tokens = [t.strip(".,;:").lower() for t in text.split()]
    if not tokens:
        return False
    has_code = any(t in {"cod", "codice", "code"} for t in tokens)
    has_desc = any(t in {"descr", "descrizione", "description"} for t in tokens)
    if has_code and has_desc:
        return True
    hits = sum(1 for t in tokens if t in HEADER_TOKENS)
    if hits >= 4 and hits >= len(tokens) * 0.5:
        return True
    # "2 LED €" is a column caption, not a product title.
    alpha = [t for t in tokens if re.search(r"[a-z]{3,}", t)]
    if len(alpha) < 2 and any(t in {"€", "eur", "euro"} for t in tokens):
        return True
    return False


def _split_title_segment(line: TextLine, page: PageText) -> list[Word]:
    """Title words up to a very wide corridor (side notes), not every space."""
    words = sorted(line.words, key=lambda w: w.x0)
    segments: list[list[Word]] = []
    gap_limit = max(40.0, page.width * 0.12)
    for word in words:
        if segments and word.x0 - segments[-1][-1].x1 <= gap_limit:
            segments[-1].append(word)
        else:
            segments.append([word])
    return segments[0] if segments else []


TITLE_STOP_RE = re.compile(
    r"(?i)\b(?:dimensions?|dimensioni|caratteristiche|tecniche|open call|"
    r"thickness|acoustic|classfication|classification)\b"
)


def _page_title(page: PageText) -> str:
    """Largest meaningful title in the top third, without prices or headers."""
    lines = [ln for ln in page.lines() if clean_text(ln.text)]
    top = []
    for ln in lines:
        if ln.y >= page.height * 0.34:
            continue
        text = clean_text(ln.text)
        if SPEC_RE.match(text) or _is_header_like(text):
            continue
        if ln.words and _is_code_token(ln.words[0].text, first=True):
            continue
        if len(re.findall(r"\d+(?:[.,]\d+)?", text)) >= 3 and ln.height < 24:
            continue
        top.append(ln)
    if not top:
        return ""
    for best in sorted(top, key=lambda ln: (ln.height, -ln.y), reverse=True):
        segment = _split_title_segment(best, page)
        if not segment:
            continue
        text = clean_text(" ".join(w.text for w in segment))
        text = CURRENCY_PRICE_RE.sub("", text).strip(" -–—|,;:")
        text = _strip_trailing_price(text, best, page)
        text = TITLE_STOP_RE.split(text)[0].strip(" -–—|,;:.")
        if not text or text[:1].islower():
            continue
        if len(text) > 90 or len(text.split()) > 14:
            continue
        return text
    return ""


def _strip_trailing_price(text: str, line: TextLine, page: PageText) -> str:
    """Drop a number sitting at the right margin (``... SELF 89``)."""
    words = line.words
    if len(words) < 2:
        return text
    last = words[-1]
    previous = words[-2]
    if not re.fullmatch(r"\d[\d.,]*", _strip_token(last.text)):
        return text
    if last.x0 - previous.x1 < page.width * 0.12:
        return text
    cut = text.rfind(last.text)
    if cut <= 0:
        return text
    return text[:cut].rstrip(" -–—|,;:")


def _panel_description(page: PageText, x0: float, y0: float,
                       panel_width: float,
                       code_centers: list[float] | None = None) -> str:
    """First descriptive lines below a code, inside the same card/panel."""
    parts: list[str] = []
    for line in sorted(page.lines(), key=lambda ln: ln.y):
        if line.y <= y0 or line.y - y0 > 600:
            continue
        center = (line.x0 + line.x1) / 2.0
        if code_centers:
            owner = min(code_centers, key=lambda cx: abs(cx - center))
            my_owner = min(code_centers, key=lambda cx: abs(cx - x0))
            if owner != my_owner:
                continue
        elif line.x1 < x0 - 10 or line.x0 > x0 + panel_width:
            continue
        text = clean_text(line.text)
        if not text or len(text.split()) < 3:
            continue
        if SPEC_RE.match(text) or CURRENCY_RE.search(text):
            continue
        if _explicit_code(text):
            continue
        if text.upper() == text:
            continue
        if text not in parts:
            parts.append(text)
        if len(parts) >= 2:
            break
    return " | ".join(parts)


def _description_above(page: PageText, x: float, y: float) -> str:
    """Reuse the card description when a code sits alone on its row.

    Cards are usually laid out as ``description`` / ``cm. kg.`` / code+price,
    so the text that belongs to a code-only row is the block above the nearest
    specification line, not simply the nearest text above the code.
    """
    right_panel = x > page.width / 2.0
    lines = sorted(page.lines(), key=lambda ln: ln.y)

    def same_panel(line: TextLine) -> bool:
        return ((line.x0 + line.x1) / 2.0 > page.width / 2.0) == right_panel

    def usable(line: TextLine) -> bool:
        text = clean_text(line.text)
        return bool(
            text and len(text.split()) >= 3 and text.upper() != text
            and not SPEC_RE.match(text) and not CURRENCY_RE.search(text)
            and not _explicit_code(text)
        )

    anchor: TextLine | None = None
    for line in lines:
        if line.y >= y - 2:
            continue
        if y - line.y > 240:
            continue
        if SPEC_RE.match(clean_text(line.text)) and same_panel(line):
            anchor = line
    limit = anchor.y if anchor else y
    window = 100.0 if anchor else 60.0
    parts: list[str] = []
    for line in reversed(lines):
        if line.y >= limit - 1:
            continue
        if limit - line.y > window:
            break
        if not same_panel(line) or not usable(line):
            continue
        parts.append(clean_text(line.text))
        if len(parts) >= 4:
            break
    parts.reverse()
    return " | ".join(parts[:2])


def _continuation_text(page: PageText, y0: float, next_y: float | None) -> str:
    """Lower-case continuation of a wrapped description (``inter-`` + ``namente``)."""
    parts: list[str] = []
    for line in sorted(page.lines(), key=lambda ln: ln.y):
        if line.y <= y0 + 1 or line.y - y0 > 34:
            continue
        if next_y is not None and line.y >= next_y - 1:
            continue
        text = clean_text(line.text)
        if not text or SPEC_RE.match(text) or CURRENCY_RE.search(text):
            continue
        if _explicit_code(text):
            continue
        if not text[:1].islower():
            break
        if text not in parts:
            parts.append(text)
        if len(parts) >= 2:
            break
    return " ".join(parts)


def _merge_description(text: str, extra: str) -> str:
    if not extra:
        return text
    if text.endswith("-"):
        return text[:-1] + extra
    return (text + " " + extra).strip()


def _make_table(columns: list[Column], rows: list[Row], *, title: str,
                profile: Profile, page_no: int) -> Table | None:
    rows = [r for r in rows if any(c.strip() for c in r.cells)]
    if not rows:
        return None
    names: set[str] = set()
    for column in columns:
        base = column.name or "Colonna"
        name = base
        suffix = 2
        while name in names:
            name = f"{base} {suffix}"
            suffix += 1
        names.add(name)
        column.name = name
    return Table(columns=columns, rows=rows, pages=[page_no], title=title,
                 profile=profile.id, source="adaptive")


# ---------------------------------------------------------------------------
# Strategy 1: product rows (code + description + prices on one line)
# ---------------------------------------------------------------------------

def product_row_table(page: PageText, profile: Profile,
                      page_no: int | None = None,
                      title: str | None = None) -> Table | None:
    """Extract rows such as ``B25  Base Leggera ...  89``."""
    pno = page_no or page.number
    title = _page_title(page) if title is None else title
    candidates: list[dict] = []

    for visual in _visual_rows(page):
        words = _glue_numeric_words(
            [w for ln in visual for w in ln.words if not w.is_blank()]
        )
        if not words:
            continue
        segments = _segment_words(words, page)
        for seg_index, segment in enumerate(segments):
            if not segment:
                continue
            explicit = _explicit_code(" ".join(w.text for w in segment))
            code = explicit
            code_index = None
            if code:
                for i, word in enumerate(segment):
                    if code.upper() in _strip_token(word.text).upper():
                        code_index = i
                        break
            if code_index is None:
                for i, word in enumerate(segment[:2]):
                    if _is_code_token(word.text, first=(i == 0)):
                        code = _strip_token(word.text)
                        code_index = i
                        break
            if not code or code_index is None:
                continue

            # prices at the end of this segment ("B25 ... 89")
            trailing = _trailing_prices(segment)
            prices: list[tuple[int, str, float, Word]] = [
                (i, raw, value, segment[i]) for i, raw, value in trailing
            ]
            local_prices = bool(prices)
            # or a numeric-only panel to the right on the same visual row
            if not prices:
                for following in segments[seg_index + 1:]:
                    if _marker_segment(following):
                        continue
                    if _segment_has_code(following):
                        break
                    pure = _pure_price_segment(following)
                    if pure:
                        prices = pure
                        break
                    # text between code and its price panel is the description
                    break
            if not prices:
                continue
            first_price_index = prices[0][0]
            if local_prices and first_price_index <= code_index:
                continue
            desc_end = first_price_index if local_prices else len(segment)
            description = " ".join(
                w.text for w in segment[code_index + 1:desc_end]
            )
            description = clean_text(description).strip(" |,;:")
            candidates.append({
                "code": code,
                "description": description,
                "prices": [(raw, value, word.cx) for _, raw, value, word in prices],
                "y": min(w.y0 for w in segment),
                "x": segment[code_index].x0,
            })

    if not candidates:
        return None

    # A short token such as "G1"/"V6" is usually a finish variant of the
    # product code above it: it repeats the same prices and must not become a
    # second product.  Real short codes (e.g. "B25") keep their own prices.
    long_codes = [
        (c["y"], tuple(raw for raw, _v, _cx in c["prices"]))
        for c in candidates if len(c["code"]) >= 4
    ]
    filtered: list[dict] = []
    for candidate in candidates:
        if len(candidate["code"]) <= 3:
            prices = tuple(raw for raw, _v, _cx in candidate["prices"])
            variant = any(
                0 < candidate["y"] - y <= 260 and prices == other_prices
                for y, other_prices in long_codes
            )
            if variant:
                continue
        filtered.append(candidate)
    if not filtered:
        return None

    filtered.sort(key=lambda c: c["y"])
    for index, candidate in enumerate(filtered):
        next_y = filtered[index + 1]["y"] if index + 1 < len(filtered) else None
        extra = _continuation_text(page, candidate["y"], next_y)
        if extra:
            candidate["description"] = _merge_description(
                candidate["description"], extra
            )
        if not candidate["description"]:
            candidate["description"] = _description_above(
                page, candidate["x"], candidate["y"]
            )

    all_centers = [cx for c in filtered for _, _, cx in c["prices"]]
    centers = _cluster_centers(all_centers)
    n_prices = max(1, len(centers))
    price_names = ["Prezzo"] + [f"Prezzo {i + 1}" for i in range(1, n_prices)]

    columns = [
        Column("Prodotto", 0, 0, kind="text"),
        Column("Descrizione", 0, 0, kind="text"),
        Column("Codice", 0, 0, kind="text"),
    ] + [Column(name, 0, 0, kind="price") for name in price_names]

    rows: list[Row] = []
    seen: set[tuple] = set()
    for candidate in filtered:
        cells = [title, candidate["description"], candidate["code"]]
        cells += [""] * n_prices
        for raw, _value, cx in candidate["prices"]:
            index = min(range(len(centers)), key=lambda k: abs(centers[k] - cx))
            if not cells[3 + index]:
                cells[3 + index] = raw
        key = (candidate["code"], candidate["description"], tuple(cells[3:]))
        if key in seen:
            continue
        seen.add(key)
        rows.append(Row(cells=cells, page=pno, y0=candidate["y"]))
    return _make_table(columns, rows, title=title, profile=profile, page_no=pno)


# ---------------------------------------------------------------------------
# Strategy 2: code and price in separate blocks (product cards)
# ---------------------------------------------------------------------------

def vertical_pair_table(page: PageText, profile: Profile,
                        page_no: int | None = None,
                        title: str | None = None) -> Table | None:
    """Pair labelled codes with labelled prices on the same card/panel."""
    pno = page_no or page.number
    title = _page_title(page) if title is None else title
    codes: list[tuple[str, TextLine]] = []
    prices: list[tuple[float, str, Word, float, float]] = []

    for line in page.lines():
        text = clean_text(line.text)
        if not text:
            continue
        for segment in _segment_words(_glue_numeric_words(line.words), page):
            segment_text = clean_text(" ".join(w.text for w in segment))
            for match in CURRENCY_PRICE_RE.finditer(segment_text):
                raw = match.group(1) or match.group(2) or ""
                value = parse_number(raw.rstrip(".,"))
                if value is None or value < 1:
                    continue
                word = next(
                    (w for w in segment if raw and raw in w.text), segment[0]
                )
                prices.append((value, raw, word, segment[0].x0, segment[-1].x1))
        code = _labeled_code(text)
        if code:
            codes.append((code, line))
    if not codes or not prices:
        return None

    proposals: list[tuple[float, int, int]] = []
    mid = page.width / 2.0
    max_dy = page.height * 0.62
    for ci, (_code, line) in enumerate(codes):
        code_center = (line.x0 + line.x1) / 2.0
        line_center_y = line.y + line.height / 2.0
        # Codes inside a long description (component codes such as "cod. 171")
        # are far below the sellable code/price block: ignore them.
        if not any(abs(word.cy - line_center_y) <= max_dy
                   for _v, _r, word, _x0, _x1 in prices):
            continue
        code_panel = code_center > mid
        for pi, (_value, _raw, word, price_x0, price_x1) in enumerate(prices):
            outside = max(0.0, price_x0 - code_center, code_center - price_x1)
            price_panel = (price_x0 + price_x1) / 2.0 > mid
            panel_penalty = 0.0 if code_panel == price_panel else 200.0
            dx = abs(word.cx - line.x0)
            dy = abs(word.cy - line_center_y)
            if dx > page.width * 0.9 or dy > max_dy:
                continue
            score = dy + dx * 0.8 + outside * 2.0 + panel_penalty
            proposals.append((score, ci, pi))
    proposals.sort()

    used_codes: set[int] = set()
    used_prices: set[int] = set()
    rows: list[Row] = []
    panel_width = page.width * 0.45
    code_centers = [(line.x0 + line.x1) / 2.0 for _code, line in codes]
    for _score, ci, pi in proposals:
        if ci in used_codes or pi in used_prices:
            continue
        used_codes.add(ci)
        used_prices.add(pi)
        code, line = codes[ci]
        _value, raw, _word, _px0, _px1 = prices[pi]
        same_line = " ".join(w.text for w in line.words)
        description = clean_text(CODE_LABEL_RE.sub("", same_line))
        if not description:
            description = _panel_description(
                page, line.x0, line.y + line.height / 2.0, panel_width,
                code_centers,
            )
        rows.append(Row(cells=[title, description, code, raw], page=pno,
                        y0=line.y))
    if not rows:
        return None
    code_x = {code: line.x0 for code, line in codes}
    rows.sort(key=lambda r: (r.y0, code_x.get(r.cells[2], 0.0)))
    columns = [
        Column("Prodotto", 0, 0, kind="text"),
        Column("Descrizione", 0, 0, kind="text"),
        Column("Codice", 0, 0, kind="text"),
        Column("Prezzo", 0, 0, kind="price"),
    ]
    return _make_table(columns, rows, title=title, profile=profile, page_no=pno)


# ---------------------------------------------------------------------------
# Strategy 3: stitch ruled header/body tables (PyMuPDF find_tables)
# ---------------------------------------------------------------------------

def _words_in_bbox(page, bbox) -> list[tuple[float, float, str]]:
    if not bbox:
        return []
    x0, y0, x1, y1 = bbox
    out: list[tuple[float, float, str]] = []
    try:
        raw = page.get_text("words")
    except Exception:
        return out
    for item in raw:
        x, y, x2, y2, text = item[0], item[1], item[2], item[3], str(item[4])
        cx, cy = (x + x2) / 2.0, (y + y2) / 2.0
        if x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1:
            out.append((round(cy, 1), x, text))
    out.sort()
    return out


def _cell_text(page, bbox) -> str:
    text = clean_text(" ".join(t for _y, _x, t in _words_in_bbox(page, bbox)))
    return text.strip('"“”').strip()


def _header_columns(page, table, matrix) -> list[tuple[str, float]] | None:
    """Return (name, centre) for a single-row header table, else None."""
    if not matrix or len(matrix) != 1:
        return None
    row = getattr(table, "rows", None)
    cells = getattr(row[0], "cells", None) if row else None
    if not cells:
        return None
    header: list[tuple[str, float]] = []
    canonical_hits = 0
    for index, bbox in enumerate(cells):
        if not bbox or index >= len(matrix[0]):
            continue
        text = clean_text(matrix[0][index] or "")
        if not text:
            continue
        name = canonical_header(text, None) or text
        if canonical_header(text, None):
            canonical_hits += 1
        if CURRENCY_RE.search(text) and name == text:
            name = "Prezzo"
        header.append((name, (bbox[0] + bbox[2]) / 2.0))
    if len(header) < 2:
        return None
    if canonical_hits < 1 and not any(n == "Prezzo" for n, _ in header):
        return None
    return header


def _is_header_row(row: list[str]) -> bool:
    """A repeated header inside a body table."""
    filled = [clean_text(c) for c in row if clean_text(c)]
    if len(filled) < 2:
        return False
    hits = sum(1 for cell in filled if canonical_header(cell, None))
    return hits >= max(1, len(filled) // 3)


def stitched_ruled_tables(fitz_page, page: PageText, profile: Profile,
                          page_no: int, title: str = "") -> list[Table]:
    """Join header-only tables with the ruled body tables below them."""
    if fitz_page is None:
        return []
    if len(page.bands) < 3 and len(page.hlines) < 12:
        return []
    try:
        found = list(fitz_page.find_tables().tables)
    except Exception:
        return []
    infos: list[tuple[float, object, list[list[str]]]] = []
    for table in found:
        try:
            matrix = table.extract()
        except Exception:
            continue
        if matrix:
            infos.append((float(table.bbox[1]), table, matrix))
    infos.sort(key=lambda item: item[0])

    out: list[Table] = []
    used: set[int] = set()
    for index, (_y, header_table, header_matrix) in enumerate(infos):
        if index in used:
            continue
        header = _header_columns(page, header_table, header_matrix)
        if header is None:
            continue
        body_tables: list[object] = []
        last_bottom = float(header_table.bbox[3])
        for j in range(index + 1, len(infos)):
            _y2, body, body_matrix = infos[j]
            bbox = body.bbox
            overlap = min(bbox[2], header_table.bbox[2]) - max(bbox[0], header_table.bbox[0])
            width = max(1.0, min(bbox[2] - bbox[0],
                                  header_table.bbox[2] - header_table.bbox[0]))
            if overlap / width < 0.6 or bbox[1] - last_bottom > 40:
                break
            if len(body_matrix) == 1 and _is_header_row(body_matrix[0]):
                break
            body_tables.append(body)
            last_bottom = float(bbox[3])
            used.add(j)
        if not body_tables:
            continue
        used.add(index)

        names = [name for name, _cx in header]
        columns = [Column(name, 0, 0, kind="text") for name in names]
        rows: list[Row] = []
        for body in body_tables:
            for body_row in getattr(body, "rows", []) or []:
                cells = getattr(body_row, "cells", None) or []
                record = [""] * len(header)
                for bbox in cells:
                    if not bbox:
                        continue
                    text = _cell_text(fitz_page, bbox)
                    if not text:
                        continue
                    cx = (bbox[0] + bbox[2]) / 2.0
                    best = min(range(len(header)), key=lambda k: abs(header[k][1] - cx))
                    record[best] = (record[best] + " " + text).strip()
                if not any(record):
                    continue
                if _is_header_row(record):
                    continue
                if sum(1 for c in record if c.strip()) < 2:
                    continue
                if not any(re.search(r"\d", c) for c in record):
                    continue
                rows.append(Row(cells=record, page=page_no))
        if not rows:
            continue
        for ci, column in enumerate(columns):
            values = [r.cells[ci] for r in rows]
            column.kind = column_kind(column.name, values)
        table = _make_table(columns, rows, title=title, profile=profile,
                            page_no=page_no)
        if table:
            out.append(table)
    return out


# ---------------------------------------------------------------------------
# Adaptive chooser
# ---------------------------------------------------------------------------

def table_score(tables: list[Table]) -> float:
    """A rough quality score used to choose between candidate strategies."""
    if not tables:
        return 0.0
    rows = sum(len(t.data_rows()) for t in tables)
    if not rows:
        return 0.0
    columns = max(len(t.columns) for t in tables)
    price_cells = 0
    code_cells = 0
    for table in tables:
        headers = table.headers
        price_idx = [i for i, c in enumerate(table.columns) if c.kind == "price"]
        code_idx = [i for i, h in enumerate(headers) if h == "Codice"]
        for row in table.data_rows():
            price_cells += sum(
                1 for i in price_idx
                if i < len(row.cells) and parse_number(row.cells[i]) is not None
            )
            code_cells += sum(
                1 for i in code_idx
                if i < len(row.cells) and (row.cells[i] or "").strip()
            )
    filled = sum(len([c for c in r.cells if c.strip()]) for t in tables for r in t.data_rows())
    density = filled / max(1, rows * columns)
    return rows * 3 + min(columns, 9) + price_cells * 2 + code_cells + density * 2


def adaptive_tables(page: PageText, profile: Profile, *, fitz_page=None,
                    page_no: int | None = None) -> tuple[list[Table], str]:
    """Try every adaptive strategy and return the best tables + technique."""
    pno = page_no or page.number
    title = _page_title(page)
    candidates: list[tuple[list[Table], str]] = []

    ruled = stitched_ruled_tables(fitz_page, page, profile, pno, title)
    if ruled:
        ruled_rows = sum(len(t.data_rows()) for t in ruled)
        canonical = sum(
            1 for t in ruled for c in t.columns
            if c.kind == "price" or canonical_header(c.name, None)
        )
        # A body stitched under a real header is far more reliable than column
        # guessing, so it wins whenever it contains more than a single row.
        if ruled_rows >= 2 and canonical >= 2:
            return ruled, "rules"
        candidates.append((ruled, "rules"))

    row_table = product_row_table(page, profile, pno, title)
    if row_table:
        candidates.append(([row_table], "rows"))

    vertical = vertical_pair_table(page, profile, pno, title)
    if vertical:
        candidates.append(([vertical], "vertical"))

    if not candidates:
        return [], "none"
    # A real ruled table is more trustworthy than guessed row columns.
    best_tables, best_technique = max(
        candidates,
        key=lambda item: table_score(item[0]) + (4.0 if item[1] == "rules" else 0.0),
    )
    return best_tables, best_technique


def page_format_hint(page: PageText) -> str:
    """Cheap classifier used for reporting pages without any table."""
    text = " ".join(w.text for w in page.words[:200])
    if CURRENCY_RE.search(text) and re.search(r"\b(?:cod|code|art)\b", text, re.IGNORECASE):
        return "vertical"
    if re.search(r"\b(?:EUR|EURO|€)\s*\d", text, re.IGNORECASE):
        return "rows"
    if len(page.hlines) >= 3 or len(page.bands) >= 3:
        return "rules"
    return "none"
