"""Text normalisation, number parsing and column-name inference."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable
from typing import Protocol


class SupportsProfile(Protocol):
    aliases: dict[str, str]
    text_fixes: list[tuple[str, str]]
    group_patterns: list[str]
    badge_patterns: list[str]
    ignore_patterns: list[str]
    description_overrides: dict[str, str]
    product_code_overrides: dict[str, str]
    section_fixes: dict[str, str]


# Canonical field names used in the exported workbook.
CANONICAL = [
    "Sezione",
    "Prodotto",
    "Gruppo",
    "Descrizione",
    "Codice",
    "Struttura",
    "Componenti",
    "Formato",
    "Elementi",
    "Misure",
    "Imballaggio",
    "Kg",
    "Prezzo",
    "Note",
]

# Multilingual aliases for the header row of a price list.
GENERIC_ALIASES: dict[str, str] = {}


def _key(text: str) -> str:
    s = (text or "").strip().lower()
    s = s.replace("€", " eur ").replace("%", " % ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[\u00a0\s]+", " ", s)
    s = re.sub(r"^[^\w]+|[^\w%]+$", "", s)
    return s.strip()


def _add(names: Iterable[str], canonical: str) -> None:
    for n in names:
        GENERIC_ALIASES[_key(n)] = canonical


_add(
    [
        "descrizione", "descrizione articolo", "descrizione prodotto", "description",
        "descr.", "descr", "descriz.", "descriz",
        "product description", "item description", "bezeichnung", "artikelbeschreibung",
        "descripción", "descripcion", "désignation", "designation", "libellé",
        "prodotto", "product", "artikel", "producto",
        "prodotto/servizio", "denominazione",
    ],
    "Descrizione",
)
_add(
    [
        "codice", "cod.", "cod", "codice articolo", "codice prodotto", "codice modello",
        "modello", "model", "code", "item code", "product code", "article code",
        "artikelnummer", "art.-nr.", "art.nr.", "artikel nr", "bezeichnung code",
        "código", "codigo", "référence", "reference", "ref.", "ref", "sku", "ean",
        "codice ean", "barcode", "codice a barre", "articolo", "article", "item",
        "articolo codice", "cod. articolo",
    ],
    "Codice",
)
_add(
    ["prezzo", "prezzo unitario", "prezzo di listino", "prezzo listino", "price",
     "unit price", "list price", "prix", "prix unitaire", "precio", "precio unitario",
     "importo", "preis", "verkaufspreis", "value", "valore", "prezzo €", "prezzo eur",
     "price €", "pvp", "prezzo al pubblico", "€", "eur", "euro", "prezzo euro",
     "prezzo/euro", "prezzo in euro"],
    "Prezzo",
)
_add(
    ["misure", "misura", "dimensioni", "dimension", "dimensions", "size", "sizes",
     "abmessungen", "mass", "maße", "masse", "medidas", "dimensiones", "formato",
     "formati", "format", "formats", "lungh. x largh. x h", "dimensioni cm"],
    "Misure",
)
_add(
    ["kg", "peso", "peso kg", "weight", "weight kg", "poids", "gewicht", "peso netto",
     "net weight", "peso lordo", "gross weight", "peso (kg)", "peso in kg"],
    "Kg",
)
_add(
    ["imballaggio", "imballo", "packaging", "packing", "verpackung", "emballage",
     "embalaje", "confezione", "colli", "cartoni", "pz per cartone", "pezzi per cartone",
     "quantità per imballo", "q.tà imballo"],
    "Imballaggio",
)
_add(
    ["struttura", "structure", "struktur", "estructura", "struttura/colore",
     "struttura colore", "colore struttura", "finitura struttura", "frame"],
    "Struttura",
)
_add(
    ["componenti", "componente", "components", "component", "komponenten",
     "composants", "componentes", "finitura", "finiture", "finish", "finishes",
     "colore", "colori", "color", "colour", "farbe", "couleur", "acabado",
     "materiale", "material", "materiali", "comp", "comp.", "compon.",
     "componente/i"],
    "Componenti",
)
_add(
    ["formato", "formati", "format", "formats", "misura formato", "formato cm"],
    "Formato",
)
_add(
    ["elementi", "elemento", "elements", "elementos", "accessori", "accessories",
     "optional", "opzionali", "moduli", "moduli aggiuntivi"],
    "Elementi",
)
_add(
    ["gruppo", "gruppo componente", "group", "gruppe", "groupe", "grupo", "categoria",
     "category", "kategorie", "famiglia", "family", "sottocategoria"],
    "Gruppo",
)
_add(
    ["sezione", "section", "bereich", "seccion", "sección", "reparto", "linea", "line"],
    "Sezione",
)
_add(
    ["note", "nota", "notes", "note aggiuntive", "annotazioni", "bemerkung",
     "observaciones", "remarques", "comment", "commento", "descrizione estesa"],
    "Note",
)
_add(
    ["quantità", "quantita", "q.tà", "qta", "qty", "quantity", "quantité", "quantite",
     "menge", "cantidad", "pz", "pezzi", "pcs", "pieces"],
    "Quantità",
)
_add(
    ["unità", "unita", "unità di misura", "um", "u.m.", "unit", "unité", "unidad",
     "einheit", "measure unit"],
    "Unità",
)
_add(
    ["sconto", "sconti", "discount", "rabatt", "remise", "descuento", "sconto %"],
    "Sconto",
)
_add(
    ["iva", "vat", "tva", "mwst", "aliquota iva", "iva %"],
    "IVA",
)


def clean_text(text: str) -> str:
    if not text:
        return ""
    s = text.replace("\u00a0", " ").replace("\u2013", "-").replace("\u2014", "-")
    s = s.replace("\ufb01", "fi").replace("\ufb02", "fl")
    s = "".join(c for c in s if c == "\n" or c == "\t" or ord(c) >= 32)
    s = re.sub(r"[ \t\r\n]+", " ", s)
    return s.strip()


def tidy(text: str, profile: SupportsProfile | None = None) -> str:
    """Apply profile fixes + conservative OCR clean-up."""
    s = clean_text(text)
    if not s:
        return ""
    if profile:
        for pat, rep in profile.text_fixes:
            try:
                s = re.sub(pat, rep, s, flags=re.IGNORECASE)
            except re.error:
                continue
    s = re.sub(r"\s+([,;.:])", r"\1", s)
    s = re.sub(r"([,;.:])(?=[A-Za-zÀ-ÿ])", r"\1 ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def canonical_header(text: str, profile: SupportsProfile | None = None,
                     aliases: dict[str, str] | None = None) -> str | None:
    key = _key(text)
    if not key:
        return None
    table: dict[str, str] = {}
    table.update(GENERIC_ALIASES)
    if profile:
        table.update(profile.aliases)
    if aliases:
        table.update({_key(k): v for k, v in aliases.items()})
    if key in table:
        return table[key]
    # tolerate "Prezzo €", "Cod. articolo", "Peso (kg)" and small OCR slips
    compact = key.replace(" ", "")
    for alias, canon in sorted(table.items(), key=lambda kv: len(kv[0]), reverse=True):
        a = alias.replace(" ", "")
        if not a or len(a) < 2:
            continue
        if compact == a:
            return canon
        if len(a) >= 3 and (compact.startswith(a) or compact.endswith(a)):
            return canon
    if len(key) >= 4:
        for alias, canon in sorted(table.items(), key=lambda kv: len(kv[0]), reverse=True):
            if len(alias) >= 4 and alias in key:
                return canon
    # generic stems for languages not covered above
    stems = [
        ("prezz", "Prezzo"), ("price", "Prezzo"), ("prix", "Prezzo"),
        ("precio", "Prezzo"), ("preis", "Prezzo"), ("import", "Prezzo"),
        ("descri", "Descrizione"), ("bezeich", "Descrizione"), ("designa", "Descrizione"),
        ("codic", "Codice"), ("code", "Codice"), ("cod.", "Codice"),
        ("artikel", "Codice"), ("referen", "Codice"), ("modell", "Codice"),
        ("misur", "Misure"), ("dimens", "Misure"), ("abmess", "Misure"),
        ("weight", "Kg"), ("peso", "Kg"), ("poids", "Kg"), ("gewicht", "Kg"),
        ("imball", "Imballaggio"), ("packag", "Imballaggio"), ("verpack", "Imballaggio"),
        ("quant", "Quantità"), ("qty", "Quantità"), ("menge", "Quantità"),
        ("strutt", "Struttura"), ("component", "Componenti"), ("finitu", "Componenti"),
    ]
    for stem, canon in stems:
        if key.startswith(stem):
            return canon
    return None


def is_header_candidate(text: str, profile: SupportsProfile | None = None) -> bool:
    return canonical_header(text, profile) is not None


def parse_number(value: object) -> float | None:
    """Parse Italian/English price and weight strings into a float."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    # dimensions, ranges and multi-value cells are not single numbers
    if re.search(r"\d\s*[×xX]\s*\d", s):
        return None
    if re.search(r"[|/]", s) and len(re.findall(r"\d+(?:[.,]\d+)?", s)) >= 2:
        return None
    s = s.replace("\u00a0", " ").replace("−", "-").replace("–", "-")
    s = re.sub(r"(?i)\b(eur|euro|€|usd|chf)\b", "", s)
    s = s.replace("€", "").replace("£", "").replace("$", "")
    s = re.sub(r"(?i)\b(kg|kgs|g|gr|pz|pcs|cm|mm|lt|l)\b\.?", "", s)
    # prices/weights are numeric; codes and descriptions are not
    if re.search(r"[A-Za-zÀ-ÿ]", s):
        return None
    s = s.replace(" ", "")
    s = re.sub(r"[^\d,.\-+]", "", s)
    if not s or s in {"-", "+", ".", ","}:
        return None
    sign = -1.0 if s.startswith("-") else 1.0
    s = s.lstrip("+-")
    if not s:
        return None
    if "," in s and "." in s:
        # the last separator is the decimal one
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2:
            left, right = parts
            # "1,234" with a 3-digit group is a thousand separator; prices
            # normally have 1-2 decimals. Keep Italian comma as decimal when
            # the left side is not a short thousands group.
            if len(right) == 3 and 1 <= len(left) <= 3 and not left.startswith("0"):
                s = left + right
            else:
                s = left + "." + right
        else:
            s = s.replace(",", "")
    elif "." in s:
        parts = s.split(".")
        if len(parts) == 2:
            left, right = parts
            if len(right) == 3 and 1 <= len(left) <= 3 and not left.startswith("0"):
                s = left + right
            else:
                s = left + "." + right
        else:
            s = s.replace(".", "")
    try:
        v = float(s) * sign
    except ValueError:
        return None
    if not math.isfinite(v):
        return None
    return v


def is_numeric_token(text: str) -> bool:
    if not text:
        return False
    return parse_number(text) is not None and bool(re.search(r"\d", text))


def numeric_ratio(values: Iterable[str]) -> float:
    vals = [v for v in values if v and v.strip()]
    if not vals:
        return 0.0
    hits = sum(1 for v in vals if is_numeric_token(v))
    return hits / len(vals)


def column_kind(name: str, values: Iterable[str]) -> str:
    key = _key(name)
    vals = list(values)
    if any(x in key for x in ("prezz", "price", "prix", "precio", "preis", "importo",
                              "valore", "€", "eur", "pvp")):
        return "price"
    if key in {"kg", "peso", "weight", "poids", "gewicht", "quantita", "quantità",
               "qty", "quantity", "quantite", "menge", "cantidad"}:
        return "number"
    if numeric_ratio(vals) >= 0.8 and any(re.search(r"\d", v) for v in vals):
        return "number"
    return "text"


def is_group_label(text: str, profile: SupportsProfile | None = None) -> bool:
    t = clean_text(text)
    if not t:
        return False
    for pat in (profile.group_patterns if profile else []):
        try:
            if re.match(pat, t, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    if re.match(r"^(componente|componenti|gruppo|gruppi|accessori|optional|opzionali|"
                r"categoria|famiglia|linea|serie|modello|finitura|colore)\b",
                t, flags=re.IGNORECASE):
        return True
    letters = [c for c in t if c.isalpha()]
    if letters and len(t) <= 80:
        upper = sum(1 for c in letters if c.isupper()) / len(letters)
        if upper >= 0.85 and len(t.split()) <= 8:
            return True
    return False


def is_badge_text(text: str, profile: SupportsProfile | None = None) -> bool:
    t = clean_text(text)
    if not t:
        return False
    for pat in (profile.badge_patterns if profile else []):
        try:
            if re.search(pat, t, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return bool(re.search(r"visualizz|foto prodotto|prezzo sistema|codice sistema",
                          t, flags=re.IGNORECASE))


def is_ignored_text(text: str, profile: SupportsProfile | None = None) -> bool:
    t = clean_text(text)
    if not t:
        return False
    for pat in (profile.ignore_patterns if profile else []):
        try:
            if re.match(pat, t, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def sanitize_sheet_name(name: str, fallback: str = "Dati") -> str:
    s = clean_text(name)
    s = re.sub(r"[\\/*?:\[\]]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .'")
    if not s:
        s = fallback
    return s[:31]


def unique_sheet_names(names: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in names:
        base = sanitize_sheet_name(raw)
        candidate = base
        i = 2
        while candidate.lower() in {x.lower() for x in out}:
            suffix = f" ({i})"
            candidate = base[: 31 - len(suffix)].rstrip() + suffix
            i += 1
        out.append(candidate)
    return out


def safe_filename(name: str, fallback: str = "listino") -> str:
    s = clean_text(name)
    s = re.sub(r"[\\/:*?\"<>|]+", "-", s)
    s = re.sub(r"\s+", " ", s).strip(" .-")
    return s or fallback
