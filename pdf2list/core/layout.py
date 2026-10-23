"""Layout analysis: find price-list tables and turn them into rows/columns.

The engine is deliberately independent of the text source (PDF text layer or
OCR).  It looks for a header line, rebuilds the column grid from the header
word positions, then assigns every word of the page to the nearest column.
This is the generalised version of the approach used for the Fernova listino.
"""

from __future__ import annotations

import logging
import math
import re
import statistics
from dataclasses import dataclass, field

from .models import Column, PageText, Row, Table, TextLine, Word, cell_text
from .normalize import (
    canonical_header,
    clean_text,
    column_kind,
    is_badge_text,
    is_group_label,
    is_ignored_text,
    is_numeric_token,
    numeric_ratio,
    parse_number,
    tidy,
    _key,
)
from .profiles import Profile

log = logging.getLogger(__name__)

TITLE_WORDS = re.compile(
    r"\b(tabella|listino|indice|condizioni|legenda|catalogo|prezzario|prezzi e componenti|"
    r"price list|preisliste|tarif|index)\b",
    re.IGNORECASE,
)


@dataclass
class _Label:
    text: str
    x0: float
    x1: float
    canonical: str | None
    y: float


@dataclass
class _Header:
    lines: list[TextLine]
    labels: list[_Label]
    columns: list[Column]
    score: float
    y0: float
    y1: float


@dataclass
class _BlockCandidate:
    line: TextLine
    score: float
    labels: list[_Label] = field(default_factory=list)
    header: _Header | None = None


def _median_height(words: list[Word]) -> float:
    hs = [w.height for w in words if 0 < w.height < 60]
    return statistics.median(hs) if hs else 8.0


def _merge_words_into_labels(line: TextLine, profile: Profile,
                             median_h: float) -> list[_Label]:
    words = [w for w in line.words
             if not is_badge_text(w.text, profile) and not is_ignored_text(w.text, profile)]
    words.sort(key=lambda w: w.x0)
    if not words:
        return []
    gap = max(2.5, median_h * 0.45)
    groups: list[list[Word]] = []
    for w in words:
        if groups and w.x0 - max(x.x1 for x in groups[-1]) <= gap:
            groups[-1].append(w)
        else:
            groups.append([w])
    labels: list[_Label] = []
    for g in groups:
        text = clean_text(" ".join(w.text for w in g))
        if not text:
            continue
        canon = canonical_header(text, profile)
        if canon is None:
            # keep plausible free-form header labels, drop numbers/punctuation
            alnum = sum(c.isalnum() for c in text)
            if alnum < 2 or len(text) > 45:
                continue
            if is_numeric_token(text):
                continue
        labels.append(
            _Label(text=text, x0=min(w.x0 for w in g), x1=max(w.x1 for w in g),
                   canonical=canon, y=line.y)
        )
    return labels


def _tabular_density(lines: list[TextLine], after_y: float) -> int:
    n = 0
    for ln in lines:
        if ln.y <= after_y:
            continue
        nums = sum(1 for w in ln.words if is_numeric_token(w.text))
        if nums >= 2:
            n += 1
        if n >= 8:
            break
    return n


def _dark_cover(page: PageText, y0: float, y1: float, x0: float, x1: float) -> float:
    width = max(1.0, x1 - x0)
    covered = 0.0
    for b in page.bands:
        if not b.dark:
            continue
        if b.y1 < y0 - 2 or b.y0 > y1 + 2:
            continue
        ov = min(b.x1, x1) - max(b.x0, x0)
        if ov > 0:
            covered += ov
    return min(1.0, covered / width)


def _score_line(line: TextLine, page: PageText, profile: Profile,
                median_h: float, all_lines: list[TextLine]) -> _BlockCandidate | None:
    labels = _merge_words_into_labels(line, profile, median_h)
    if len(labels) < 2:
        return None
    text = clean_text(" ".join(l.text for l in labels))
    if not text:
        return None
    if is_badge_text(line.text, profile):
        return None
    if any(is_badge_text(l.text, profile) for l in labels):
        return None
    if line.y > page.height * 0.94 or line.y < page.height * 0.02:
        return None
    alias = [l for l in labels if l.canonical]
    alias_count = len(alias)
    # very long prose lines are not headers
    if max((len(l.text) for l in labels), default=0) > 42 and alias_count < 3:
        return None
    if re.search(r"[;!?]", text) and alias_count < 3:
        return None
    title_like = bool(TITLE_WORDS.search(text))
    if alias_count < 2 and not (
        title_like is False
        and len(labels) >= 3
        and all(l.text.upper() == l.text for l in labels if any(c.isalpha() for c in l.text))
    ):
        return None

    score = alias_count * 5.0
    score += min(3.0, len(labels) * 0.4)
    if title_like:
        score -= 9.0
    # header bars are usually darker than the rows
    cover = _dark_cover(page, line.y - 1, line.y + line.height + 1, line.x0, line.x1)
    if cover > 0.35:
        score += 6.0 * min(1.0, cover)
    # a real header is followed by tabular data
    density = _tabular_density(all_lines, line.y + line.height)
    score += min(4.0, density * 0.5)
    if density < 2:
        score -= 4.0
    # uppercase header labels are common
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.75:
        score += 1.0
    # headers are horizontally spread out
    if len(labels) >= 2:
        gaps = [labels[i + 1].x0 - labels[i].x0 for i in range(len(labels) - 1)]
        if gaps and statistics.median(gaps) > median_h * 2.0:
            score += 1.5
    if alias_count < 2:
        score -= 6.0
    if score < 6.0:
        return None
    return _BlockCandidate(line=line, score=score, labels=labels)


def _find_candidates(page: PageText, profile: Profile) -> list[_BlockCandidate]:
    lines = page.lines()
    if not lines:
        return []
    median_h = _median_height(page.words)
    candidates: list[_BlockCandidate] = []
    for ln in lines:
        cand = _score_line(ln, page, profile, median_h, lines)
        if cand:
            candidates.append(cand)
    candidates.sort(key=lambda c: (c.line.y, -c.score))
    # collapse candidates that belong to the same two-line header block:
    # keep the strongest one, the block builder will add the neighbours
    chosen: list[_BlockCandidate] = []
    for cand in sorted(candidates, key=lambda c: -c.score):
        duplicate = False
        for c in chosen:
            same_y = abs(cand.line.y - c.line.y) < max(6.0, median_h * 1.8)
            overlap = (min(cand.line.x1, c.line.x1) - max(cand.line.x0, c.line.x0))
            min_w = max(1.0, min(cand.line.x1 - cand.line.x0, c.line.x1 - c.line.x0))
            if same_y and overlap > min_w * 0.2:
                duplicate = True
                break
        if not duplicate:
            chosen.append(cand)
    chosen.sort(key=lambda c: (c.line.y, c.line.x0))
    return chosen


def _merge_header_block(cand: _BlockCandidate, page: PageText,
                        profile: Profile) -> _Header:
    lines = page.lines()
    median_h = _median_height(page.words)
    # locate the line object
    try:
        idx = lines.index(cand.line)
    except ValueError:
        idx = min(range(len(lines)), key=lambda i: abs(lines[i].y - cand.line.y))
    block = [lines[idx]]
    max_gap = max(5.0, median_h * 0.6)
    for direction in (-1, 1):
        j = idx + direction
        while 0 <= j < len(lines):
            prev = block[0] if direction < 0 else block[-1]
            cur = lines[j]
            gap = (prev.y - (cur.y + cur.height)) if direction < 0 else (
                cur.y - (prev.y + prev.height)
            )
            if gap > max_gap:
                break
            labels = _merge_words_into_labels(cur, profile, median_h)
            aliases = [l for l in labels if l.canonical]
            text = clean_text(cur.text)
            title_like = bool(TITLE_WORDS.search(text))
            if is_badge_text(text, profile):
                break
            if len(labels) < 1 or title_like:
                break
            if not aliases:
                break
            # never merge a side-by-side block into this header
            block_x0 = min(l.x0 for l in block)
            block_x1 = max(l.x1 for l in block)
            if min(block_x1, cur.x1) - max(block_x0, cur.x0) <= 0:
                break
            # do not pull in a data row or a group heading
            numeric = sum(1 for w in cur.words if is_numeric_token(w.text))
            if numeric >= 2:
                break
            if any(re.search(r"[€$£]", w.text) for w in cur.words):
                break
            if len(labels) == 1 and is_group_label(text, profile):
                break
            block.append(cur)
            j += direction
            if len(block) >= 4:
                break
    block.sort(key=lambda l: (l.y, l.x0))
    # read header labels line by line
    per_line: list[list[_Label]] = [
        _merge_words_into_labels(ln, profile, median_h) for ln in block
    ]
    labels = [lab for ls in per_line for lab in ls]
    labels = _dedupe_labels(labels, per_line)
    columns = _labels_to_columns(labels, per_line, page, profile)
    y0 = min(l.y for l in block)
    y1 = max(l.y + l.height for l in block)
    return _Header(lines=block, labels=labels, columns=columns,
                   score=cand.score, y0=y0, y1=y1)


def _dedupe_labels(labels: list[_Label], per_line: list[list[_Label]]) -> list[_Label]:
    """Remove group headings ("CODICE" over 3 sub-columns) and duplicates."""
    # a group label is an upper-line label whose x-centre sits above several
    # labels of a lower line
    drop: set[int] = set()
    for i, lab in enumerate(labels):
        upper = lab.y
        lower_lines = [ls for ls in per_line if ls and ls[0].y > upper + 1]
        if not lower_lines:
            continue
        lower = [l for ls in lower_lines for l in ls]
        letters = [c for c in lab.text if c.isalpha()]
        is_caps = letters and sum(c.isupper() for c in letters) / len(letters) > 0.8
        if is_caps:
            # A group heading ("CODICE") can be narrower than the columns it
            # spans, so expand its box generously and count the child labels.
            pad = max(40.0, (lab.x1 - lab.x0) * 2.0)
            children = [l for l in lower
                        if l.x1 >= lab.x0 - pad and l.x0 <= lab.x1 + pad]
            if len(children) >= 2:
                drop.add(i)
                continue
        # exact duplicate of a label on another line at the same x
        for j, other in enumerate(labels):
            if i == j:
                continue
            if abs(lab.x0 - other.x0) < 6 and abs(lab.x1 - other.x1) < 12:
                if lab.canonical and other.canonical and lab.canonical == other.canonical:
                    # keep the one with more complete text
                    if len(lab.text) < len(other.text):
                        drop.add(i)
                    elif len(lab.text) == len(other.text) and lab.y > other.y:
                        drop.add(i)
                    break
    out = [l for i, l in enumerate(labels) if i not in drop]
    # labels that are still identical in text and position
    unique: list[_Label] = []
    for lab in sorted(out, key=lambda l: (l.x0, l.y)):
        if unique and abs(lab.x0 - unique[-1].x0) < 4 and _key(lab.text) == _key(unique[-1].text):
            continue
        unique.append(lab)
    return unique


def _labels_to_columns(labels: list[_Label], per_line: list[list[_Label]],
                       page: PageText, profile: Profile) -> list[Column]:
    labels = sorted(labels, key=lambda l: l.x0)
    if len(labels) < 2:
        return []
    # merge labels that are closer than a character width (OCR splits)
    merged: list[_Label] = []
    for lab in labels:
        if merged and lab.x0 - merged[-1].x1 < 2.5:
            merged[-1].x1 = max(merged[-1].x1, lab.x1)
            merged[-1].text = (merged[-1].text + " " + lab.text).strip()
            merged[-1].canonical = canonical_header(merged[-1].text, profile)
        else:
            merged.append(lab)
    labels = merged
    cols: list[Column] = []
    used: dict[str, int] = {}
    for i, lab in enumerate(labels):
        name = lab.canonical or clean_text(lab.text)
        name = re.sub(r"\s+", " ", name).strip(" .:;|")
        if not name:
            name = f"Colonna {i + 1}"
        base = name
        if base in used:
            used[base] += 1
            name = f"{base} ({used[base]})"
        else:
            used[base] = 1
        x0 = lab.x0
        x1 = labels[i + 1].x0 - 4 if i + 1 < len(labels) else page.width - 12
        if x1 <= x0:
            x1 = x0 + 40
        cols.append(Column(name=name, x0=x0, x1=x1, original=lab.text))
    return cols


def _column_name_map(columns: list[Column]) -> list[str]:
    return [c.name for c in columns]


def _is_repeated_header(rownames: list[str], header: list[str]) -> bool:
    vals = [clean_text(v) for v in rownames if clean_text(v)]
    if len(vals) < 2:
        return False
    keys = {_key(h) for h in header if _key(h)}
    hits = 0
    for v in vals:
        k = _key(v)
        if k in keys:
            hits += 1
    return hits >= max(2, math.ceil(len(header) * 0.5))


def _table_bottom(page: PageText, header: _Header, headers: list[_Header]) -> float:
    bottom = page.height * 0.945
    for other in headers:
        if other is header:
            continue
        if other.y0 <= header.y0 + 2:
            continue
        hx0, hx1 = header.labels[0].x0, header.labels[-1].x1
        ox0, ox1 = other.labels[0].x0, other.labels[-1].x1
        if min(hx1, ox1) - max(hx0, ox0) <= 0:
            continue
        bottom = min(bottom, other.y0 - 4)
    # A page with very few horizontal rules uses them as section separators,
    # not as table cell borders: they are a reliable end-of-table marker.
    if len(page.hlines) <= 5:
        hx0, hx1 = header.labels[0].x0, header.labels[-1].x1
        for y, x0, x1 in page.hlines:
            if y <= header.y1 + 6:
                continue
            if min(hx1, x1) - max(hx0, x0) <= 0:
                continue
            bottom = min(bottom, y - 4)
            break
    return max(header.y1 + 10, bottom)


def _row_groups(words: list[Word], median_h: float) -> list[list[Word]]:
    if not words:
        return []
    words = sorted(words, key=lambda w: (w.y0, w.x0))
    tol = max(3.0, min(median_h * 0.62, 8.0))
    groups: list[list[Word]] = []
    for w in words:
        placed = False
        for g in reversed(groups[-3:]):
            gtop = min(x.y0 for x in g)
            gbot = max(x.y1 for x in g)
            # vertical overlap or a gap small enough to be a wrapped line
            if w.y0 <= gbot + tol and w.y1 >= gtop - tol:
                g.append(w)
                placed = True
                break
        if not placed:
            groups.append([w])
    groups.sort(key=lambda g: (min(w.y0 for w in g), min(w.x0 for w in g)))
    return groups


def _find_boundaries(columns: list[Column], words: list[Word]) -> list[float]:
    """Find the white corridors between columns using the data rows.

    Header labels alone are not enough: the description column is usually much
    wider than its title, and centred numeric cells can start left of their
    header.  The gaps that are empty in every row are the reliable signal.
    """
    if len(columns) < 2:
        return []
    anchors = [c.x0 for c in columns]
    intervals = sorted((w.x0, w.x1) for w in words if w.width > 0)
    bounds: list[float] = []
    for i in range(len(columns) - 1):
        a, b = anchors[i], anchors[i + 1]
        span = max(1.0, b - a)
        lo = a + span * 0.25
        hi = b + span * 0.18
        merged: list[list[float]] = []
        for x0, x1 in intervals:
            if x1 < lo or x0 > hi:
                continue
            cx0, cx1 = max(x0, lo), min(x1, hi)
            if merged and cx0 <= merged[-1][1] + 0.6:
                merged[-1][1] = max(merged[-1][1], cx1)
            else:
                merged.append([cx0, cx1])
        gaps: list[tuple[float, float]] = []
        prev = lo
        for x0, x1 in merged:
            if x0 - prev >= 2.0:
                gaps.append((prev, x0))
            prev = max(prev, x1)
        if hi - prev >= 2.0:
            gaps.append((prev, hi))
        if gaps:
            gap = max(
                gaps,
                key=lambda g: (g[1] - g[0],
                               -abs((g[0] + g[1]) / 2.0 - (b - span * 0.04))),
            )
            boundary = min((gap[0] + gap[1]) / 2.0, b - span * 0.07)
        else:
            boundary = b - min(6.0, span * 0.04)
        bounds.append(min(max(boundary, a + span * 0.1), b + span * 0.08))
    return bounds


def _assign_words(words: list[Word], columns: list[Column],
                  boundaries: list[float] | None = None) -> dict[int, list[Word]]:
    if not columns:
        return {}
    if boundaries is None:
        boundaries = [(columns[i].x1 + columns[i + 1].x0) / 2.0
                      for i in range(len(columns) - 1)]
    edges = [-1e12, *boundaries, 1e12]
    first_boundary = boundaries[0] if boundaries else 1e12
    cells: dict[int, list[Word]] = {}
    for w in words:
        # Centred columns (prices, weights, short codes) are assigned by the
        # word centre; the wide description column is left-aligned.
        if w.x0 < first_boundary:
            probe = w.x0
        elif is_numeric_token(w.text) or len(w.text) <= 12:
            probe = w.cx
        else:
            probe = w.x0
        idx = 0
        for i in range(len(columns)):
            if probe >= edges[i] and probe < edges[i + 1]:
                idx = i
                break
        if w.x0 < columns[0].x0 - 120:
            continue
        if idx == len(columns) - 1 and w.x0 > columns[-1].x1 + 120:
            continue
        cells.setdefault(idx, []).append(w)
    return cells


def _row_cells(words: list[Word], columns: list[Column], profile: Profile,
               median_h: float, boundaries: list[float] | None = None) -> list[str]:
    cells_map = _assign_words(words, columns, boundaries)
    out = [""] * len(columns)
    for idx, ws in cells_map.items():
        text = cell_text(ws)
        out[idx] = tidy(text, profile)
    return out


def _cluster_centers(ws: list[Word], tol: float) -> list[float]:
    if not ws:
        return []
    ys = sorted(w.cy for w in ws)
    clusters: list[list[float]] = [[ys[0]]]
    for y in ys[1:]:
        if y - clusters[-1][-1] <= tol:
            clusters[-1].append(y)
        else:
            clusters.append([y])
    return [sum(c) / len(c) for c in clusters]


def _row_anchors(columns: list[Column], words: list[Word],
                 boundaries: list[float], median_h: float) -> tuple[list[float], float]:
    """Find one vertical anchor per table row.

    Multi-line cells make pure y-gap clustering unreliable, so we use columns
    that normally contain exactly one value per row (price, code, weight) and
    union their line centres.
    """
    assigned = _assign_words(words, columns, boundaries)
    cands: list[tuple[float, int, list[float]]] = []
    for idx, ws in assigned.items():
        if len(ws) < 2:
            continue
        centers = _cluster_centers(ws, max(1.8, median_h * 0.5))
        if len(centers) < 2:
            continue
        col = columns[idx]
        name = _key(col.name)
        if col.kind == "price":
            weight = 6.0
        elif name in {"codice", "articolo", "modello", "code", "art", "sku"}:
            weight = 5.0
        elif col.kind == "number":
            weight = 3.0
        else:
            weight = 1.0
        avg = len(ws) / max(1, len(centers))
        score = weight + min(8.0, len(centers) * 0.5) - max(0.0, avg - 3.0) * 6.0
        cands.append((score, idx, centers))
    if not cands:
        return [], median_h
    cands.sort(reverse=True)
    top = cands[:2]
    all_centers = sorted(c for _, _, centers in top for c in centers)
    merged: list[float] = []
    for c in all_centers:
        if merged and c - merged[-1] <= max(3.5, median_h * 0.6):
            merged[-1] = (merged[-1] + c) / 2.0
        else:
            merged.append(c)
    merged.sort()
    diffs = [merged[i + 1] - merged[i] for i in range(len(merged) - 1)]
    pitch = statistics.median(diffs) if diffs else median_h * 1.6
    return merged, max(median_h * 1.1, pitch)


def _group_entries(page: PageText, columns: list[Column], header_names: list[str],
                   area_ids: set[int], profile: Profile,
                   median_h: float) -> list[tuple[float, str, set[int]]]:
    entries: list[tuple[float, str, set[int]]] = []
    for ln in page.lines():
        ws = [w for w in ln.words if id(w) in area_ids]
        if not ws:
            continue
        text = clean_text(" ".join(w.text for w in ws))
        if not text or is_badge_text(text, profile) or is_ignored_text(text, profile):
            continue
        if _is_repeated_header([text], header_names):
            continue
        if len(ws) > 10 or not is_group_label(text, profile):
            continue
        if len(columns) > 1 and max(w.x0 for w in ws) > columns[1].x0 + 40:
            continue
        entries.append((ln.y, text, {id(w) for w in ws}))
    # merge a group label that wrapped onto two lines
    entries.sort(key=lambda e: e[0])
    out: list[tuple[float, str, set[int]]] = []
    for e in entries:
        if out and e[0] - out[-1][0] <= max(3.0, median_h * 0.8):
            y, text, ids = out[-1]
            out[-1] = (y, (text + " " + e[1]).strip(), ids | e[2])
        else:
            out.append(e)
    return out


def _cell_row(g: list[Word], columns: list[Column], profile: Profile,
              boundaries: list[float]) -> tuple[list[str], float, float, float]:
    cells = _row_cells(g, columns, profile, _median_height(g), boundaries)
    y0 = min(w.y0 for w in g)
    y1 = max(w.y1 for w in g)
    conf = sum(w.conf for w in g) / max(1, len(g))
    return cells, y0, y1, conf


def _extract_rows(page: PageText, header: _Header, bottom: float,
                  profile: Profile) -> list[Row]:
    columns = header.columns
    if not columns:
        return []
    median_h = _median_height(page.words)
    # badge lines are info boxes, not table rows
    badge_lines: set[int] = set()
    for ln in page.lines():
        if is_badge_text(ln.text, profile) or is_ignored_text(ln.text, profile):
            for w in ln.words:
                badge_lines.add(id(w))
    for w in page.words:
        if is_badge_text(w.text, profile):
            badge_lines.add(id(w))

    x_left = columns[0].x0 - 80
    x_right = columns[-1].x1 + 120
    words = [
        w for w in page.words
        if id(w) not in badge_lines
        and w.y0 >= header.y1 - 1
        and w.y0 < bottom
        and w.y1 > header.y1 - 8
        and w.x1 >= x_left
        and w.x0 <= x_right
        and w.y0 < page.height * 0.955
    ]
    if not words:
        return []
    header_keys = header.labels
    header_names = [c.name for c in columns]
    boundaries = _find_boundaries(columns, words)

    area_ids = {id(w) for w in words}
    group_entries = _group_entries(page, columns, header_names, area_ids,
                                   profile, median_h)
    group_ids = set().union(*(e[2] for e in group_entries)) if group_entries else set()
    row_words = [w for w in words if id(w) not in group_ids]

    anchors, pitch = _row_anchors(columns, row_words, boundaries, median_h)
    data_rows: list[tuple[float, Row]] = []

    def push(cells: list[str], y0: float, y1: float, conf: float) -> None:
        nonempty = [(i, v) for i, v in enumerate(cells) if v.strip()]
        if not nonempty:
            return
        if _is_repeated_header([v for _, v in nonempty], header_names):
            return
        if len(nonempty) >= 2 and all(
            _key(v) in {_key(l.text) for l in header_keys}
            or _key(v) in {_key(c.original) for c in columns}
            for _, v in nonempty
        ):
            return
        if len(nonempty) == 1 and (
            is_badge_text(nonempty[0][1], profile)
            or is_ignored_text(nonempty[0][1], profile)
        ):
            return
        data_rows.append((y0, Row(cells, page.number, kind="data",
                                  confidence=conf, y0=y0)))

    if len(anchors) >= 2:
        buckets: dict[int, list[Word]] = {i: [] for i in range(len(anchors))}
        for w in row_words:
            idx = min(range(len(anchors)), key=lambda i: abs(w.cy - anchors[i]))
            if abs(w.cy - anchors[idx]) <= max(pitch * 0.72, median_h * 1.4):
                buckets[idx].append(w)
        for i in range(len(anchors)):
            g = buckets[i]
            if not g:
                continue
            cells, y0, y1, conf = _cell_row(g, columns, profile, boundaries)
            push(cells, y0, y1, conf)
    else:
        # fallback: y-gap clustering with a tight tolerance
        tol = max(1.8, min(median_h * 0.38, 4.5))
        local = _row_groups(row_words, median_h)
        for g in local:
            if not g:
                continue
            cells, y0, y1, conf = _cell_row(g, columns, profile, boundaries)
            push(cells, y0, y1, conf)

    # insert group headings and give each data row its group context
    items: list[tuple[float, Row]] = [
        (y, Row([text] + [""] * (len(columns) - 1), page.number,
                kind="group", group=text, y0=y))
        for y, text, _ in group_entries
    ]
    items.extend(data_rows)
    items.sort(key=lambda item: item[0])
    rows: list[Row] = []
    current_group = ""
    for _, row in items:
        if row.kind == "group":
            current_group = row.group
            rows.append(row)
        else:
            row.group = current_group
            rows.append(row)

    # join continuation lines of wrapped cells (e.g. a two-line Ganci value)
    merged_rows: list[Row] = []
    for row in rows:
        filled = [(i, c) for i, c in enumerate(row.cells) if c.strip()]
        if (row.kind == "data" and len(filled) == 1 and merged_rows
                and merged_rows[-1].kind == "data"
                and 0 <= row.y0 - merged_rows[-1].y0 <= pitch * 1.05):
            idx, value = filled[0]
            prev = merged_rows[-1]
            if idx < len(prev.cells) and prev.cells[idx].strip():
                prev.cells[idx] = (prev.cells[idx] + " " + value).strip()
                continue
        merged_rows.append(row)
    rows = merged_rows

    # trim prose/footer fragments that follow the table with a large gap
    multi_y = max((r.y0 for r in rows
                   if r.kind == "data" and sum(1 for c in r.cells if c.strip()) >= 2),
                  default=None)
    if multi_y is not None:
        gap_limit = max(median_h * 3.0, 24.0)
        rows = [r for r in rows
                if not (r.kind == "data"
                        and sum(1 for c in r.cells if c.strip()) <= 1
                        and r.y0 > multi_y + gap_limit)]
    return rows


def _table_title(page: PageText, header: _Header, profile: Profile) -> str:
    candidates = []
    for ln in page.lines():
        if ln.y + ln.height > header.y0 + 2:
            continue
        if header.y0 - (ln.y + ln.height) > 170:
            continue
        text = clean_text(ln.text)
        if not text or is_badge_text(text, profile) or is_ignored_text(text, profile):
            continue
        if len(text) > 90 or len(text.split()) > 14:
            continue
        # skip tiny page furniture and pure numbers
        if is_numeric_token(text):
            continue
        candidates.append(ln)
    if not candidates:
        return ""
    # prefer a real table-bar title near the header; then the closest line
    def is_title_line(l: TextLine) -> bool:
        compact = re.sub(r"\s+", "", clean_text(l.text)).upper()
        return bool(TITLE_WORDS.search(clean_text(l.text))) or any(
            k in compact for k in ("TABELLA", "PREZZI", "LISTINO", "PRICELIST")
        )

    candidates.sort(key=lambda l: (0 if is_title_line(l) else 1,
                                   header.y0 - (l.y + l.height),
                                   -l.height))
    title = clean_text(candidates[0].text)
    return title


def _short_title(title: str) -> str:
    s = clean_text(title)
    # table bars are often OCR'd without spaces: COBA—TABELLAPREZZI...
    if re.search(r"(?i)(tabella|prezzi|listino)", s.replace(" ", "")):
        s = re.split(r"[—–-]", s)[0]
    s = re.sub(r"\s*[—–-]\s*(tabella|lista|listino)\b.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(tabella prezzi.*|prezzi e componenti.*)$", "", s, flags=re.IGNORECASE)
    return s.strip(" -—–:") or clean_text(title)


# --------------------------------------------------------------------------
# product cards ("Descrizione | Cod." key/value blocks)
# --------------------------------------------------------------------------

CARD_FIELD_ALIASES = {
    "articolo": "Codice", "modello": "Codice", "codice": "Codice", "cod": "Codice",
    "cod.": "Codice", "ref": "Codice", "reference": "Codice",
    "struttura": "Struttura", "corpo": "Struttura", "montanti": "Struttura",
    "componenti": "Componenti", "componente": "Componenti", "finitura": "Componenti",
    "finiture": "Componenti", "colore": "Componenti", "colori": "Componenti",
    "ganci": "Componenti", "coperchio": "Componenti", "pannelli": "Componenti",
    "ripiani": "Componenti", "sfere": "Componenti", "pomelli": "Componenti",
    "seduta": "Componenti", "schienale": "Componenti", "cuscini": "Componenti",
    "materiale": "Componenti", "tessuto": "Componenti",
    "misure": "Misure", "misura": "Misure", "dimensioni": "Misure",
    "formato": "Formato", "formati": "Formato",
    "peso": "Kg", "kg": "Kg", "capacita": "Misure", "volume": "Misure",
    "prezzo": "Prezzo", "prezzo unitario": "Prezzo",
    "descrizione": "Descrizione", "note": "Note", "nota": "Note",
    "tipologia rifiuto": "Componenti",
}

CARD_COLUMNS = ["Prodotto", "Descrizione", "Codice", "Struttura", "Componenti",
                "Formato", "Misure", "Kg", "Prezzo", "Note"]


def _card_field(label: str, profile: Profile | None = None) -> str | None:
    key = _key(label)
    if key in CARD_FIELD_ALIASES:
        return CARD_FIELD_ALIASES[key]
    canon = canonical_header(label, profile)
    if canon in CARD_COLUMNS:
        return canon
    for k in sorted(CARD_FIELD_ALIASES, key=len, reverse=True):
        if key.startswith(k) and len(k) >= 3:
            return CARD_FIELD_ALIASES[k]
    return None


def _is_card_header(h: _Header, page: PageText) -> bool:
    if len(h.columns) != 2:
        return False
    names = [c.name for c in h.columns]
    width = h.labels[-1].x1 - h.labels[0].x0
    if width > page.width * 0.52:
        return False
    first = names[0]
    second = names[1]
    if first not in {"Descrizione", "Prodotto", "Articolo", "Codice"}:
        return False
    return second in {"Codice", "Prezzo"} or first == "Descrizione"


def _card_span(h: _Header) -> tuple[float, float]:
    return h.labels[0].x0 - 45.0, h.labels[-1].x1 + 45.0


def _lines_from_words(words: list[Word]) -> list[TextLine]:
    """Group an arbitrary word set into visual lines (no page-wide merges)."""
    if not words:
        return []
    hs = sorted(w.height for w in words if 0 < w.height < 60)
    median_h = statistics.median(hs) if hs else 8.0
    words = sorted(words, key=lambda w: (w.cy, w.x0))
    groups: list[list[Word]] = []
    for w in words:
        placed = False
        for g in reversed(groups[-5:]):
            gy0 = min(x.y0 for x in g)
            gy1 = max(x.y1 for x in g)
            overlap = min(gy1, w.y1) - max(gy0, w.y0)
            min_h = max(1.0, min(gy1 - gy0, max(w.height, 1.0)))
            if overlap >= 0.34 * min_h or (
                    overlap >= 0 and abs(w.cy - (gy0 + gy1) / 2.0) <= max(1.5, median_h * 0.18)):
                g.append(w)
                placed = True
                break
        if not placed:
            groups.append([w])
    out: list[TextLine] = []
    for g in groups:
        g.sort(key=lambda w: w.x0)
        out.append(TextLine(words=g, y=min(w.y0 for w in g),
                            height=max(w.y1 for w in g) - min(w.y0 for w in g)))
    out.sort(key=lambda ln: (ln.y, ln.x0))
    return out


def _card_extract(page: PageText, h: _Header, profile: Profile,
                  headers: list[_Header]) -> Table | None:
    span0, span1 = _card_span(h)
    # card bottom: the next header in the same horizontal band, or a badge
    bottom = page.height * 0.945
    for other in headers:
        if other is h or other.y0 <= h.y0 + 5:
            continue
        o0, o1 = _card_span(other)
        if min(span1, o1) - max(span0, o0) <= 0:
            continue
        bottom = min(bottom, other.y0 - 4)
    for y, x0, x1 in page.hlines:
        if y <= h.y1 + 4:
            continue
        if min(span1, x1) - max(span0, x0) <= 0:
            continue
        bottom = min(bottom, y - 3)
    badge_text = ""
    below_lines = _lines_from_words([
        w for w in page.words
        if w.cy >= h.y1 and w.cy < bottom
        and w.x1 >= span0 - 20 and w.x0 <= span1 + 20
    ])
    for ln in below_lines:
        if is_badge_text(ln.text, profile):
            badge_text = (badge_text + " | " + clean_text(ln.text)) if badge_text else clean_text(ln.text)
            bottom = min(bottom, ln.y - 2)
    words = [w for w in page.words
             if w.y0 >= h.y1 - 1 and w.y0 < bottom
             and w.x1 >= span0 and w.x0 <= span1]
    if not words:
        return None
    median_h = _median_height(page.words)
    boundaries = _find_boundaries(h.columns, words)
    groups = _row_groups(words, median_h)
    fields: dict[str, list[str]] = {}
    extras: list[str] = []
    header_names = [c.name for c in h.columns]
    for g in groups:
        cells = _row_cells(g, h.columns, profile, median_h, boundaries)
        vals = [c.strip() for c in cells if c.strip()]
        if len(vals) < 2:
            continue
        if _is_repeated_header(vals, header_names):
            continue
        label, value = vals[0], " ".join(vals[1:]).strip()
        if not label or not value:
            continue
        if is_badge_text(label + " " + value, profile):
            continue
        # rows of a key/value card always have a short label; this drops
        # prose paragraphs that happen to be above/below the card
        if len(label) > 30 or len(label.split()) > 5:
            continue
        if len(value) > 140:
            continue
        target = _card_field(label, profile)
        if target is None:
            if len(value) > 80:
                continue
            if is_numeric_token(value):
                extras.append(f"{label}: {value}")
            else:
                fields.setdefault("Note", []).append(f"{label}: {value}")
            continue
        fields.setdefault(target, []).append(value)
    if not fields:
        return None
    if not any(k not in {"Note", "Descrizione"} for k in fields):
        return None

    # product heading + description above the card, using only words inside
    # this card's horizontal band (side-by-side cards often share a baseline)
    title = ""
    title_line = None
    title_span0, title_span1 = span0 - 160, span1 + 60
    top_limit = page.height * 0.03
    for y, x0, x1 in page.hlines:
        if y < h.y0 - 8 and min(span1, x1) - max(span0, x0) > 0:
            top_limit = max(top_limit, y + 2)
    above = _lines_from_words([
        w for w in page.words
        if w.cy < h.y0
        and w.cx >= title_span0 and w.cx <= title_span1
        and w.cy > max(top_limit, h.y0 - 540)
    ])
    unit_like = re.compile(
        r"(\d\s*[×xX]\s*\d|\bcm\b|\bmm\b|\bkg\b|\bh\.?\b|\beur\b|€)",
        re.IGNORECASE,
    )
    def is_card_header_line(txt: str) -> bool:
        key = _key(txt)
        if key.startswith("descrizione") and ("cod" in key or "prezzo" in key):
            return True
        labels = [canonical_header(w, profile) for w in txt.split()]
        return sum(1 for x in labels if x) >= 2

    def is_description_line(ln: TextLine) -> bool:
        txt = clean_text(ln.text)
        if not txt or len(txt.split()) < 6:
            return False
        if ln.height > median_h * 2.6:
            return False
        if is_badge_text(txt, profile) or is_card_header_line(txt):
            return False
        if _is_repeated_header([txt], header_names):
            return False
        if _card_field(txt.split()[0], profile) is not None:
            return False
        return True

    def clean_title(text: str) -> str:
        m = re.match(r"^((?:[A-Z0-9][A-Z0-9'’.\-]*\s+){1,3})(?=(?:[A-Z][a-zà-ÿ])|[a-zà-ÿ])",
                     text)
        if m and len(m.group(1).split()) <= 4:
            return m.group(1).strip()
        if len(text.split()) > 4 or len(text) > 35:
            m = re.search(r"\b([A-Z][A-Z'’.\-]{2,})\s?(\d{1,3})\b", text)
            if m:
                return f"{m.group(1)} {m.group(2)}"
        return text

    # find the top of the description block immediately above the card header;
    # the title is the short line directly above that block
    desc_lines: list[TextLine] = []
    last_y = h.y0 + 1000
    for ln in sorted(above, key=lambda l: -l.y):
        if not is_description_line(ln):
            continue
        if desc_lines and last_y - (ln.y + ln.height) > max(12.0, median_h * 2.2):
            break
        desc_lines.append(ln)
        last_y = ln.y
    desc_top = min((ln.y for ln in desc_lines), default=h.y0)

    for ln in sorted(above, key=lambda l: l.y):
        text = clean_title(clean_text(ln.text))
        if not text or len(text) > 70:
            continue
        if ln.height > median_h * 2.6:
            continue
        if len(text.split()) > 5:
            continue
        if text.endswith("."):
            continue
        if ln.y < page.height * 0.13 and text.upper() == text:
            continue
        if unit_like.search(text) or is_numeric_token(text):
            continue
        if is_card_header_line(text):
            continue
        first = text.split()[0]
        if _card_field(first, profile) is not None:
            continue
        if not text[0].isupper():
            continue
        if re.match(r"^(versione|disponibile|appendiabiti|struttura|componenti|"
                    r"ppl|in |con |per |adatto|fornito|colore|finitura)\b",
                    text, re.I):
            continue
        title_line = ln
        title = text
        break
    # OCR sometimes merges a card title with the first description line
    title = clean_title(title)
    title = re.sub(r"^([A-Z]{2,})(\d{1,3})$", r"\1 \2", title.strip())
    if len(title) > 32 or len(title.split()) > 5:
        title = ""

    # a badge above the header belongs to this card and ends the description
    desc_end = h.y0
    if title_line is not None:
        for ln in above:
            if ln.y > title_line.y and is_badge_text(ln.text, profile):
                desc_end = min(desc_end, ln.y - 2)
    desc_parts: list[str] = []
    if title_line is not None:
        for ln in sorted(above, key=lambda l: (l.y, l.x0)):
            if ln.y <= title_line.y + title_line.height - 4:
                continue
            if ln.y + ln.height > desc_end:
                continue
            txt = clean_text(ln.text)
            if not txt or len(txt) < 8:
                continue
            if ln.height > median_h * 2.6:
                continue
            if is_badge_text(txt, profile) or is_card_header_line(txt):
                continue
            if _is_repeated_header([txt], header_names):
                continue
            if unit_like.search(txt) and len(txt.split()) <= 6:
                continue
            first = txt.split()[0]
            if _card_field(first, profile) is not None:
                continue
            if len(txt.split()) < 3:
                continue
            desc_parts.append(txt)
    description = tidy(" ".join(desc_parts), profile)

    # split multi-variant values ("GR, NR | CR", "€ 52,00 | € 55,00")
    nvar = 1
    for k, vs in fields.items():
        if k in {"Codice", "Struttura", "Componenti", "Prezzo", "Formato"}:
            nvar = max(nvar, max((v.count("|") + 1 for v in vs), default=1))
    note = " | ".join(extras)
    if badge_text:
        note = (note + " | " + badge_text).strip(" |")
    rows: list[Row] = []
    for i in range(nvar):
        vals: dict[str, str] = {}
        for k, vs in fields.items():
            parts = []
            for v in vs:
                p = [x.strip() for x in v.split("|")]
                if (len(p) == 1 and nvar > 1 and k == "Codice"):
                    toks = [t for t in v.split() if t]
                    if (len(toks) == nvar
                            and all(re.fullmatch(r"[A-Z0-9/+.\-]{2,}", t) for t in toks)):
                        p = toks
                parts.append(p[i] if i < len(p) else (p[-1] if p else ""))
            vals[k] = " / ".join(x for x in parts if x)
        row = [""] * len(CARD_COLUMNS)
        row[0] = title
        row[1] = description or vals.get("Descrizione", "")
        for j, name in enumerate(CARD_COLUMNS[2:], start=2):
            row[j] = vals.get(name, "")
        row[-1] = note
        rows.append(Row(row, page.number, kind="data",
                        confidence=sum(w.conf for w in words) / max(1, len(words)),
                        y0=h.y0))
    if not rows:
        return None
    cols = [Column(name=n, x0=0, x1=0, kind=column_kind(n, [r.cells[i] for r in rows]))
            for i, n in enumerate(CARD_COLUMNS)]
    return Table(columns=cols, rows=rows, pages=[page.number], title=title or "Schede prodotto",
                 profile=profile.id, source="cards")


def _table_quality(table: Table) -> bool:
    data = table.data_rows()
    if not data:
        return False
    # A price list needs a price column: this also keeps index/legend pages out.
    price_idx = [i for i, c in enumerate(table.columns) if c.kind == "price"]
    if not price_idx:
        return False
    # side-by-side cards are handled separately; duplicated headers here are
    # almost always a layout artefact
    base_names = [re.sub(r"\s*\(\d+\)$", "", h).strip().lower() for h in table.headers]
    if len(base_names) != len(set(base_names)):
        return False
    multi = sum(1 for r in data if sum(1 for c in r.cells if c.strip()) >= 2)
    if len(data) == 1 and multi == 0:
        return False
    numeric_prices = 0
    numeric_cells = 0
    for r in data:
        for i, cell in enumerate(r.cells):
            if cell and parse_number(cell) is not None:
                numeric_cells += 1
                if i in price_idx:
                    numeric_prices += 1
    ratio = numeric_prices / max(1, len(data) * len(price_idx))
    if ratio < 0.34:
        return False
    if len(data) < 2 and numeric_prices < 1:
        return False
    if multi / max(1, len(data)) < 0.5:
        return False
    used_cols = set()
    for r in data:
        for i, c in enumerate(r.cells):
            if c.strip():
                used_cols.add(i)
    return len(used_cols) >= 2



def _merge_headers_horizontal(headers: list[_Header], page: PageText,
                              profile: Profile) -> list[_Header]:
    """Join a header that OCR split into two horizontal pieces.

    Example: 'Modello | Struttura | Ganci | Misure' and 'Kg | Prezzo' on the
    same table row but detected as two blocks.  Side-by-side product cards are
    separated by a much larger corridor and are left alone.
    """
    if len(headers) < 2:
        return headers
    median_h = _median_height(page.words)
    groups: list[list[_Header]] = []
    for h in sorted(headers, key=lambda x: (x.y0, x.labels[0].x0)):
        placed = False
        for g in groups:
            ref = g[0]
            close_x = (min(h.labels[0].x0, ref.labels[0].x0)
                       - max(h.labels[-1].x1, ref.labels[-1].x1))
            if not (abs(h.y0 - ref.y0) <= max(10.0, median_h * 0.9)
                    and close_x < page.width * 0.12):
                continue
            # never join two side-by-side cards: they would duplicate headers
            names = [c.name for hh in g + [h] for c in hh.columns]
            base = [re.sub(r"\s*\(\d+\)$", "", n).strip().lower() for n in names]
            if len(base) != len(set(base)):
                continue
            g.append(h)
            placed = True
            break
        if not placed:
            groups.append([h])
    out: list[_Header] = []
    for g in groups:
        if len(g) == 1:
            out.append(g[0])
            continue
        labels = [lab for h in g for lab in h.labels]
        labels = _dedupe_labels(labels, [h.labels for h in g])
        cols = _labels_to_columns(labels, [h.labels for h in g], page, profile)
        if len(cols) < 2:
            out.extend(g)
            continue
        out.append(_Header(
            lines=[ln for h in g for ln in h.lines],
            labels=labels,
            columns=cols,
            score=max(h.score for h in g),
            y0=min(h.y0 for h in g),
            y1=max(h.y1 for h in g),
        ))
    out.sort(key=lambda h: (h.y0, h.columns[0].x0))
    return out


def extract_tables(page: PageText, profile: Profile) -> list[Table]:
    """Return every table found on a single page."""
    if not page.words:
        return []
    candidates = _find_candidates(page, profile)
    headers = [_merge_header_block(c, page, profile) for c in candidates]
    headers = [h for h in headers if len(h.columns) >= 2]
    headers = _merge_headers_horizontal(headers, page, profile)
    headers.sort(key=lambda h: (h.y0, h.columns[0].x0))
    tables: list[Table] = []

    # product cards: narrow 2-column key/value blocks
    card_rows: list[Row] = []
    card_headers: list[_Header] = [h for h in headers if _is_card_header(h, page)]
    card_ids = {id(h) for h in card_headers}
    for h in card_headers:
        card = _card_extract(page, h, profile, headers)
        if card:
            card_rows.extend(card.rows)
    if card_rows:
        cols = [Column(name=n, x0=0, x1=0,
                       kind=column_kind(n, [r.cells[i] for r in card_rows]))
                for i, n in enumerate(CARD_COLUMNS)]
        tables.append(Table(columns=cols, rows=card_rows, pages=[page.number],
                            title="Schede prodotto", profile=profile.id,
                            source="cards"))

    # wide price tables
    for h in headers:
        if id(h) in card_ids:
            continue
        bottom = _table_bottom(page, h, headers)
        rows = _extract_rows(page, h, bottom, profile)
        data = [r for r in rows if r.kind == "data"]
        multi = [r for r in data if sum(1 for c in r.cells if c.strip()) >= 2]
        if len(data) < 1 or (len(data) < 2 and not multi):
            continue
        title = _table_title(page, h, profile)
        table = Table(
            columns=list(h.columns),
            rows=rows,
            pages=[page.number],
            title=title,
            profile=profile.id,
            source="header",
        )
        for ci, col in enumerate(table.columns):
            vals = [r.cells[ci] for r in data if ci < len(r.cells)]
            col.kind = column_kind(col.name, vals)
        if not _table_quality(table):
            continue
        tables.append(table)
    return tables


def table_from_matrix(matrix: list[list[str]], page_no: int,
                      profile: Profile, title: str = "") -> Table | None:
    """Build a table from a generic matrix (used by the ruled/PDF table path)."""
    matrix = [[clean_text(c or "") for c in row] for row in matrix]
    matrix = [row for row in matrix if any(c for c in row)]
    if len(matrix) < 2:
        return None
    width = max(len(r) for r in matrix)
    matrix = [r + [""] * (width - len(r)) for r in matrix]
    first = matrix[0]
    canon = [canonical_header(c, profile) for c in first]
    alias_hits = sum(1 for c in canon if c)
    if alias_hits >= max(2, width // 2):
        names = [c or f"Colonna {i + 1}" for i, c in enumerate(canon)]
        body = matrix[1:]
        original = first
    else:
        names = [f"Colonna {i + 1}" for i in range(width)]
        body = matrix
        original = [""] * width
    cols = []
    for i, name in enumerate(names):
        vals = [r[i] for r in body]
        cols.append(Column(name=name, x0=0, x1=0, original=original[i],
                           kind=column_kind(name, vals)))
    rows: list[Row] = []
    for r in body:
        if not any(c for c in r):
            continue
        if any(is_badge_text(c, profile) for c in r):
            continue
        rows.append(Row(cells=r, page=page_no, kind="data"))
    if not rows:
        return None
    return Table(columns=cols, rows=rows, pages=[page_no], title=title,
                 profile=profile.id, source="rules")


def fallback_tables(page: PageText, profile: Profile) -> list[Table]:
    """Last-resort detection for headerless/unknown tables.

    Uses numeric columns as anchors (prices, weights, quantities).  Only runs
    on pages that clearly look tabular, to avoid turning prose into tables.
    """
    lines = page.lines()
    if len(lines) < 5:
        return []
    numeric_lines = 0
    anchors: list[float] = []
    for ln in lines:
        nums = [w for w in ln.words if is_numeric_token(w.text)]
        if len(nums) >= 2:
            numeric_lines += 1
            anchors.extend(w.x0 for w in nums)
    if numeric_lines < 5 or len(anchors) < 8:
        return []
    # cluster anchors
    anchors.sort()
    clusters: list[list[float]] = []
    for x in anchors:
        if clusters and x - clusters[-1][-1] <= 14:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    clusters = [c for c in clusters if len(c) >= 3]
    if len(clusters) < 2:
        return []
    centers = sorted(statistics.median(c) for c in clusters)
    # add a description column to the left when text starts well before
    # the first numeric cluster
    all_x = [w.x0 for w in page.words if not is_numeric_token(w.text)]
    if all_x and min(all_x) < centers[0] - 40:
        centers.insert(0, min(all_x))
    names = [f"Colonna {i + 1}" for i in range(len(centers))]
    cols = [Column(name=n, x0=x, x1=(centers[i + 1] - 3 if i + 1 < len(centers)
                                     else page.width - 10))
            for i, (n, x) in enumerate(zip(names, centers))]
    median_h = _median_height(page.words)
    groups = _row_groups([w for w in page.words
                          if w.y0 > page.height * 0.08 and w.y0 < page.height * 0.94],
                         median_h)
    rows: list[Row] = []
    for g in groups:
        cells = _row_cells(g, cols, profile, median_h)
        if sum(1 for c in cells if c.strip()) >= 2:
            rows.append(Row(cells, page.number, kind="data"))
    if len(rows) < 3:
        return []
    # a real table has its description/first column populated in most rows;
    # this rejects prose blocks on image-heavy card pages
    first_filled = sum(1 for r in rows if r.cells and r.cells[0].strip())
    if first_filled / len(rows) < 0.7:
        return []
    for ci, c in enumerate(cols):
        vals = [r.cells[ci] for r in rows]
        c.kind = column_kind(c.name, vals)
    # headerless tables: the rightmost numeric column is almost always price
    if cols:
        last = cols[-1]
        vals = [r.cells[-1] for r in rows]
        if last.kind == "text" and numeric_ratio(vals) >= 0.5:
            last.kind = "price"
    title = ""
    # use the largest line in the top quarter as a label
    top = [ln for ln in lines if ln.y < page.height * 0.3]
    if top:
        best = max(top, key=lambda l: l.height)
        if best.height > median_h * 1.2 and len(best.text) < 60:
            title = clean_text(best.text)
    return [Table(columns=cols, rows=rows, pages=[page.number], title=title,
                  profile=profile.id, source="fallback",
                  warnings=["Colonne ricostruite automaticamente: verifica il risultato."])]
