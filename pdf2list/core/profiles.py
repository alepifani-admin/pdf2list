"""Extraction profiles: reusable vocabulary for recurring price lists."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import CANONICAL, _key

RESOURCE_DIR = Path(__file__).resolve().parent.parent / "resources" / "profiles"


@dataclass
class Profile:
    name: str = "Generico"
    description: str = ""
    aliases: dict[str, str] = field(default_factory=dict)
    text_fixes: list[tuple[str, str]] = field(default_factory=list)
    group_patterns: list[str] = field(default_factory=list)
    badge_patterns: list[str] = field(default_factory=list)
    ignore_patterns: list[str] = field(default_factory=list)
    description_overrides: dict[str, str] = field(default_factory=dict)
    product_code_overrides: dict[str, str] = field(default_factory=dict)
    section_fixes: dict[str, str] = field(default_factory=dict)
    currency: str = "EUR"
    id: str = "generic"

    def fix(self, text: str) -> str:
        from .normalize import tidy

        return tidy(text, self)

    def canonical(self, text: str) -> str | None:
        from .normalize import canonical_header

        return canonical_header(text, self)

    def apply_description_overrides(self, product: str, code: str, desc: str) -> str:
        key = (product or "").strip().upper()
        ck = (code or "").strip().upper()
        if ck and ck in self.description_overrides:
            return self.description_overrides[ck]
        if (key, ck) in self.product_code_overrides:
            return self.product_code_overrides[(key, ck)]
        return desc

    def apply_section_fix(self, section: str) -> str:
        s = (section or "").strip().upper()
        return self.section_fixes.get(s, section)


def _compile_fixes(raw: list) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in raw or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            pat, rep = str(item[0]), str(item[1])
        elif isinstance(item, dict):
            pat, rep = str(item.get("pattern", "")), str(item.get("replace", ""))
        else:
            continue
        if not pat:
            continue
        try:
            re.compile(pat)
        except re.error:
            continue
        out.append((pat, rep))
    return out


def profile_from_dict(data: dict, profile_id: str = "custom") -> Profile:
    pc = data.get("product_code_overrides") or {}
    pco: dict[str, str] = {}
    for k, v in pc.items():
        if isinstance(k, (list, tuple)) and len(k) == 2:
            pco[(str(k[0]).upper(), str(k[1]).upper())] = str(v)
        elif isinstance(k, str) and "::" in k:
            a, b = k.split("::", 1)
            pco[(a.strip().upper(), b.strip().upper())] = str(v)
        elif isinstance(k, str) and "|" in k:
            a, b = k.split("|", 1)
            pco[(a.strip().upper(), b.strip().upper())] = str(v)
    aliases = {}
    for k, v in (data.get("aliases") or {}).items():
        if str(v) not in CANONICAL and str(v) not in {
            "Quantità", "Unità", "Sconto", "IVA"
        }:
            continue
        aliases[_key(str(k))] = str(v)
    return Profile(
        name=str(data.get("name") or "Profilo"),
        description=str(data.get("description") or ""),
        aliases=aliases,
        text_fixes=_compile_fixes(data.get("text_fixes") or []),
        group_patterns=[str(x) for x in (data.get("group_patterns") or [])],
        badge_patterns=[str(x) for x in (data.get("badge_patterns") or [])],
        ignore_patterns=[str(x) for x in (data.get("ignore_patterns") or [])],
        description_overrides={str(k).upper(): str(v)
                               for k, v in (data.get("description_overrides") or {}).items()},
        product_code_overrides=pco,
        section_fixes={str(k).upper(): str(v)
                       for k, v in (data.get("section_fixes") or {}).items()},
        currency=str(data.get("currency") or "EUR"),
        id=str(data.get("id") or profile_id),
    )


def builtin_profiles() -> list[tuple[str, str, str]]:
    """Return (id, name, description) for the profiles shipped with the app."""
    out: list[tuple[str, str, str]] = []
    if not RESOURCE_DIR.is_dir():
        return [("generic", "Generico", "Riconoscimento universale di listini")]
    for path in sorted(RESOURCE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append((path.stem, str(data.get("name") or path.stem),
                    str(data.get("description") or "")))
    if not any(p[0] == "generic" for p in out):
        out.insert(0, ("generic", "Generico", "Riconoscimento universale di listini"))
    return out


def load_profile(profile_id: str = "generic", path: str | Path | None = None) -> Profile:
    if path:
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        return profile_from_dict(data, p.stem)
    candidate = RESOURCE_DIR / f"{profile_id}.json"
    if candidate.is_file():
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            return profile_from_dict(data, profile_id)
        except Exception:
            pass
    return Profile()


def save_profile_template(path: str | Path) -> None:
    data = {
        "id": "mio-listino",
        "name": "Mio listino",
        "description": "Profilo personalizzato: aggiungi sinonimi e correzioni.",
        "currency": "EUR",
        "aliases": {
            "codice ns": "Codice",
            "prezzo ns": "Prezzo",
        },
        "text_fixes": [
            ["\\bmonbe\\b", "gambe"],
            ["\\s{2,}", " "],
        ],
        "group_patterns": ["^COMPONENTE", "^GRUPPO", "^ACCESSORI"],
        "badge_patterns": ["visualizz", "foto prodotto"],
        "ignore_patterns": ["^pag\\.", "^listino prodotti"],
        "description_overrides": {},
        "product_code_overrides": {},
        "section_fixes": {},
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
