"""Core data structures shared by extraction, AI assistance and export."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass(slots=True)
class Word:
    """A text fragment with its bounding box in PDF points."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float = 1.0
    line_id: int = -1

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    def is_blank(self) -> bool:
        return not self.text.strip()


@dataclass(slots=True)
class Band:
    """A filled vector rectangle (table row/header background)."""

    x0: float
    y0: float
    x1: float
    y1: float
    dark: bool

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass(slots=True)
class TextLine:
    words: list[Word]
    y: float
    height: float

    @property
    def x0(self) -> float:
        return min(w.x0 for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x1 for w in self.words)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def conf(self) -> float:
        if not self.words:
            return 0.0
        return sum(w.conf for w in self.words) / len(self.words)


@dataclass
class PageText:
    number: int
    width: float
    height: float
    words: list[Word]
    has_text_layer: bool = False
    ocr_used: bool = False
    bands: list[Band] = field(default_factory=list)
    hlines: list[tuple[float, float, float]] = field(default_factory=list)
    rotation: int = 0

    def sorted_words(self) -> list[Word]:
        return sorted(self.words, key=lambda w: (round(w.cy, 1), w.x0))

    def lines(self, tolerance: float | None = None) -> list[TextLine]:
        """Group words into visual lines using vertical overlap."""
        words = [w for w in self.words if not w.is_blank()]
        if not words:
            return []
        heights = sorted(w.height for w in words if w.height > 0)
        median_h = heights[len(heights) // 2] if heights else 8.0
        tol = tolerance if tolerance is not None else max(2.0, median_h * 0.45)
        words = sorted(words, key=lambda w: (w.cy, w.x0))
        groups: list[list[Word]] = []
        for w in words:
            placed = False
            for g in reversed(groups[-4:]):
                gy0 = min(x.y0 for x in g)
                gy1 = max(x.y1 for x in g)
                overlap = min(gy1, w.y1) - max(gy0, w.y0)
                min_h = max(1.0, min(gy1 - gy0, max(w.height, 1.0)))
                # words of the same visual line normally overlap substantially;
                # two adjacent header lines (or wrapped cell lines) do not
                if overlap >= 0.34 * min_h:
                    g.append(w)
                    placed = True
                    break
                if (overlap >= 0 and abs(w.cy - (gy0 + gy1) / 2.0) <= max(1.5, median_h * 0.18)):
                    g.append(w)
                    placed = True
                    break
            if not placed:
                groups.append([w])
        lines: list[TextLine] = []
        split_threshold = self.width * 0.07

        def split_block(ws: list[Word]) -> list[list[Word]]:
            if len(ws) < 4:
                return [ws]
            gaps = [(ws[i + 1].x0 - ws[i].x1, i) for i in range(len(ws) - 1)]
            gap, i = max(gaps)
            if gap < split_threshold:
                return [ws]
            left, right = ws[: i + 1], ws[i + 1:]
            if len(left) < 2 or len(right) < 2:
                return [ws]
            return split_block(left) + split_block(right)

        for g in groups:
            g.sort(key=lambda w: w.x0)
            # Side-by-side layout blocks (e.g. two product cards) share the
            # same baseline but are separated by a wide empty corridor.
            for part in split_block(g):
                lines.append(
                    TextLine(
                        words=part,
                        y=min(w.y0 for w in part),
                        height=max(w.y1 for w in part) - min(w.y0 for w in part),
                    )
                )
        lines.sort(key=lambda ln: (ln.y, ln.x0))
        return lines


@dataclass(slots=True)
class Column:
    name: str
    x0: float
    x1: float
    original: str = ""
    kind: str = "text"  # text | number | price


@dataclass(slots=True)
class Row:
    cells: list[str]
    page: int
    kind: str = "data"  # data | group
    group: str = ""
    confidence: float = 1.0
    y0: float = 0.0


@dataclass
class Table:
    columns: list[Column]
    rows: list[Row] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    title: str = ""
    profile: str = ""
    source: str = "header"  # header | rules | ai | fallback
    warnings: list[str] = field(default_factory=list)

    @property
    def headers(self) -> list[str]:
        return [c.name for c in self.columns]

    @property
    def signature(self) -> str:
        return "|".join(_norm_sig(h) for h in self.headers)

    def data_rows(self) -> list[Row]:
        return [r for r in self.rows if r.kind == "data"]

    def to_matrix(self, include_groups: bool = False) -> list[list[str]]:
        out: list[list[str]] = []
        for r in self.rows:
            if r.kind == "group":
                if include_groups:
                    out.append([r.cells[0] if r.cells else r.group] + [""] * (len(self.columns) - 1))
                continue
            out.append(_fit(r.cells, len(self.columns)))
        return out


@dataclass
class ExtractionResult:
    source: str
    tables: list[Table] = field(default_factory=list)
    pages: int = 0
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return sum(len(t.data_rows()) for t in self.tables)

    def merge_similar(self) -> None:
        """Merge tables with the same header and compatible product title.

        Different products in a catalogue share the same columns; merging them
        blindly would lose the product context.  Tables that continue on the
        next page keep the same title and are merged.
        """
        merged: list[Table] = []
        for t in self.tables:
            target = None
            tkey = _title_key(t.title)
            for m in merged:
                if m.signature != t.signature:
                    continue
                mkey = _title_key(m.title)
                if not tkey or not mkey or tkey == mkey or tkey in mkey or mkey in tkey:
                    target = m
                    break
            if target is None:
                target = Table(
                    columns=list(t.columns),
                    rows=[],
                    pages=[],
                    title=t.title,
                    profile=t.profile,
                    source=t.source,
                    warnings=list(t.warnings),
                )
                merged.append(target)
            elif not target.title and t.title:
                target.title = t.title
            for p in t.pages or [r.page for r in t.rows[:1]]:
                if p not in target.pages:
                    target.pages.append(p)
            target.rows.extend(t.rows)
        self.tables = merged


def _title_key(title: str) -> str:
    s = (title or "").lower()
    s = s.replace("—", "-").replace("–", "-")
    s = re.sub(r"\s*-\s*(tabella|listino|prezzi|price list).*$", "", s)
    s = re.sub(r"\b(tabella prezzi|tabella|listino|prezzi e componenti)\b.*$", "", s)
    s = re.sub(r"[^a-z0-9àèéìòù]+", " ", s)
    s = " ".join(s.split())
    if s in {"", "listino", "tabella", "schede prodotto", "dati"}:
        return ""
    return s


def _fit(cells: Sequence[str], n: int) -> list[str]:
    out = list(cells[:n])
    if len(out) < n:
        out.extend([""] * (n - len(out)))
    return out


def _norm_sig(text: str) -> str:
    return " ".join(text.lower().replace("€", "").split())


def cell_text(words: Iterable[Word]) -> str:
    """Join words of one cell in reading order, merging line fragments."""
    ws = sorted(words, key=lambda w: (w.cy, w.x0))
    if not ws:
        return ""
    heights = sorted(w.height for w in ws if w.height > 0)
    multi = len(ws) > 1
    median = heights[len(heights) // 2] if heights else 8.0
    tol = max(2.0, median * 0.55)
    lines: list[list[Word]] = []
    for w in ws:
        if lines:
            last = lines[-1]
            base = sum(x.cy for x in last) / len(last)
            if abs(w.cy - base) <= tol:
                last.append(w)
                continue
        lines.append([w])
    parts: list[str] = []
    for line in lines:
        line.sort(key=lambda w: w.x0)
        parts.append(" ".join(w.text.strip() for w in line if w.text.strip()))
    # if the fragments are far apart horizontally, keep a separator
    return " ".join(p for p in parts if p).strip() if multi else parts[0].strip()
