"""Optional AI assistance (column mapping, OCR clean-up, vision fallback).

The app is fully functional offline.  AI is opt-in and only extracted text
(or, when explicitly enabled, page images) is sent to the configured provider.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from .models import Column, ExtractionResult, Row, Table
from .normalize import CANONICAL, canonical_header, column_kind, unique_sheet_names
from .profiles import Profile

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, int, int], None]


@dataclass
class ProviderPreset:
    id: str
    name: str
    base_url: str
    default_model: str
    kind: str  # openai | anthropic | gemini
    needs_key: bool = True
    vision: bool = True
    note: str = ""


PROVIDERS: list[ProviderPreset] = [
    ProviderPreset("openai", "OpenAI", "https://api.openai.com/v1",
                   "gpt-4o-mini", "openai", note="Chiave da platform.openai.com"),
    ProviderPreset("anthropic", "Anthropic (Claude)", "https://api.anthropic.com/v1",
                   "claude-3-5-haiku-latest", "anthropic",
                   note="Chiave da console.anthropic.com"),
    ProviderPreset("google", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta",
                   "gemini-2.0-flash", "gemini", note="Chiave da aistudio.google.com"),
    ProviderPreset("mistral", "Mistral", "https://api.mistral.ai/v1",
                   "mistral-small-latest", "openai", note="Chiave da console.mistral.ai"),
    ProviderPreset("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
                   "openai/gpt-4o-mini", "openai",
                   note="Una chiave per molti modelli (openrouter.ai)"),
    ProviderPreset("deepseek", "DeepSeek", "https://api.deepseek.com/v1",
                   "deepseek-chat", "openai", note="Chiave da platform.deepseek.com"),
    ProviderPreset("ollama", "Ollama (locale)", "http://localhost:11434/v1",
                   "llama3.1", "openai", needs_key=False, vision=False,
                   note="Gratuito e offline: avvia 'ollama serve'"),
    ProviderPreset("lmstudio", "LM Studio (locale)", "http://localhost:1234/v1",
                   "local-model", "openai", needs_key=False, vision=False,
                   note="Server locale compatibile OpenAI"),
    ProviderPreset("custom", "Altro (compatibile OpenAI)", "", "", "openai",
                   note="Inserisci URL base e nome modello"),
]


def provider_preset(provider_id: str) -> ProviderPreset:
    for p in PROVIDERS:
        if p.id == provider_id:
            return p
    return PROVIDERS[-1]


@dataclass
class AISettings:
    enabled: bool = False
    provider: str = "openai"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout: int = 90
    map_columns: bool = True
    clean_rows: bool = False
    vision_fallback: bool = False
    max_rows: int = 300
    temperature: float = 0.0


class AIError(RuntimeError):
    pass


def redact(text: str, key: str) -> str:
    if key and len(key) > 6:
        return text.replace(key, "***")
    return text


def _extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                return obj
            except Exception:
                continue
    raise AIError("La risposta AI non contiene JSON valido.")


class AIClient:
    def __init__(self, settings: AISettings) -> None:
        self.s = settings
        self.preset = provider_preset(settings.provider)
        self.kind = self.preset.kind
        self.base_url = (settings.base_url or self.preset.base_url).rstrip("/")
        self.model = settings.model or self.preset.default_model
        if not self.base_url:
            raise AIError("URL base del provider non configurato.")
        if not self.model:
            raise AIError("Modello AI non configurato.")
        if self.preset.needs_key and not settings.api_key:
            raise AIError("Chiave API mancante. Inseriscila in Impostazioni → AI.")

    # ------------------------------------------------------------------
    def _post(self, url: str, payload: dict, headers: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=self.s.timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            detail = redact(detail, self.s.api_key)
            raise AIError(f"Errore HTTP {exc.code} dal provider AI: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AIError(f"Impossibile raggiungere il provider AI: {exc.reason}") from exc
        except Exception as exc:
            raise AIError(f"Errore di rete AI: {exc}") from exc
        try:
            return json.loads(body)
        except Exception as exc:
            raise AIError(f"Risposta AI non valida: {body[:300]}") from exc

    def chat(self, user_text: str, *, system: str = "", json_mode: bool = True,
             image_png: bytes | None = None, max_tokens: int = 4096) -> str:
        if self.kind == "anthropic":
            return self._chat_anthropic(user_text, system, image_png, max_tokens)
        if self.kind == "gemini":
            return self._chat_gemini(user_text, system, image_png, max_tokens)
        return self._chat_openai(user_text, system, json_mode, image_png, max_tokens)

    # ------------------------------------------------------------------
    def _chat_openai(self, user_text: str, system: str, json_mode: bool,
                     image_png: bytes | None, max_tokens: int) -> str:
        url = self.base_url + "/chat/completions"
        if image_png:
            content = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(image_png).decode("ascii")
                }},
            ]
        else:
            content = user_text
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.s.temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and not image_png:
            payload["response_format"] = {"type": "json_object"}
        headers = {}
        if self.s.api_key:
            headers["Authorization"] = f"Bearer {self.s.api_key}"
        try:
            resp = self._post(url, payload, headers)
        except AIError:
            if "response_format" in payload:
                payload.pop("response_format", None)
                resp = self._post(url, payload, headers)
            else:
                raise
        try:
            return str(resp["choices"][0]["message"]["content"] or "")
        except Exception as exc:
            raise AIError(f"Risposta AI inattesa: {str(resp)[:300]}") from exc

    def _chat_anthropic(self, user_text: str, system: str,
                        image_png: bytes | None, max_tokens: int) -> str:
        url = self.base_url + "/messages"
        content: list[dict] = []
        if image_png:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(image_png).decode("ascii"),
                },
            })
        content.append({"type": "text", "text": user_text})
        payload: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": self.s.temperature,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            payload["system"] = system
        headers = {
            "x-api-key": self.s.api_key,
            "anthropic-version": "2023-06-01",
        }
        resp = self._post(url, payload, headers)
        try:
            blocks = resp.get("content") or []
            return "".join(str(b.get("text", "")) for b in blocks
                           if isinstance(b, dict) and b.get("type") == "text")
        except Exception as exc:
            raise AIError(f"Risposta AI inattesa: {str(resp)[:300]}") from exc

    def _chat_gemini(self, user_text: str, system: str,
                     image_png: bytes | None, max_tokens: int) -> str:
        safe_model = urllib.parse.quote(self.model, safe="")
        url = f"{self.base_url}/models/{safe_model}:generateContent"
        key = urllib.parse.quote(self.s.api_key, safe="")
        if key:
            url += f"?key={key}"
        parts: list[dict] = [{"text": user_text}]
        if image_png:
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": base64.b64encode(image_png).decode("ascii"),
                }
            })
        payload: dict = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": self.s.temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        resp = self._post(url, payload, {})
        try:
            cands = resp.get("candidates") or []
            parts = cands[0]["content"]["parts"]
            return "".join(str(p.get("text", "")) for p in parts)
        except Exception as exc:
            raise AIError(f"Risposta AI inattesa: {str(resp)[:300]}") from exc

    # ------------------------------------------------------------------
    def test(self) -> str:
        text = self.chat(
            "Rispondi esclusivamente con la parola OK.",
            system="Sei un test di connessione. Non aggiungere altro.",
            json_mode=False,
            max_tokens=16,
        )
        return text.strip()[:200]


# --------------------------------------------------------------------------
# AI tasks
# --------------------------------------------------------------------------

MAP_SYSTEM = (
    "Sei un esperto di listini prezzi. Rispondi sempre e solo con JSON valido, "
    "senza spiegazioni e senza markdown."
)

CLEAN_SYSTEM = (
    "Sei un correttore di testi OCR di listini prezzi italiani. Rispondi sempre "
    "e solo con JSON valido. Non inventare dati, non cambiare numeri, codici, "
    "prezzi o dimensioni. Correggi solo spaziatura, lettere e accenti evidenti."
)

VISION_SYSTEM = (
    "Sei un motore OCR specializzato in listini prezzi. Trascrivi la tabella "
    "della pagina senza inventare nulla. Rispondi solo con JSON valido."
)


def map_table_headers(client: AIClient, table: Table,
                      progress: ProgressFn | None = None) -> Table:
    headers = table.headers
    if not headers:
        return table
    sample = [[r.cells[i] if i < len(r.cells) else "" for i in range(len(headers))]
              for r in table.data_rows()[:4]]
    prompt = (
        "Mappa le intestazioni di questa tabella di listino ai campi canonici.\n"
        f"Campi canonici: {', '.join(CANONICAL)}.\n"
        "Se un'intestazione non corrisponde a nessun campo, usa null.\n"
        "Mantieni una corrispondenza uno-a-uno dove possibile.\n"
        f"Intestazioni: {json.dumps(headers, ensure_ascii=False)}\n"
        f"Prime righe: {json.dumps(sample, ensure_ascii=False)}\n"
        'Formato risposta: {"mapping": {"intestazione originale": "Campo canonico o null"}}'
    )
    raw = client.chat(prompt, system=MAP_SYSTEM, json_mode=True, max_tokens=1200)
    data = _extract_json(raw)
    mapping = data.get("mapping", data if isinstance(data, dict) else {})
    if not isinstance(mapping, dict):
        return table
    used: dict[str, int] = {}
    new_cols: list[Column] = []
    canon_lookup = {c.lower(): c for c in CANONICAL}
    for i, col in enumerate(table.columns):
        target = mapping.get(col.original) or mapping.get(col.name)
        if target is None and col.name not in CANONICAL:
            target = canonical_header(col.name)
        if isinstance(target, str):
            target = canon_lookup.get(target.strip().lower(), target)
        if isinstance(target, str) and target in CANONICAL:
            name = target
        else:
            name = col.name
        base = name
        if base in used:
            used[base] += 1
            name = f"{base} ({used[base]})"
        else:
            used[base] = 1
        new_cols.append(Column(name=name, x0=col.x0, x1=col.x1,
                               original=col.original or col.name, kind=col.kind))
    table.columns = new_cols
    for ci, col in enumerate(table.columns):
        vals = [r.cells[ci] for r in table.data_rows() if ci < len(r.cells)]
        col.kind = column_kind(col.name, vals)
    if progress:
        progress("AI: intestazioni mappate", 0, 1)
    return table


def clean_table_rows(client: AIClient, table: Table, max_rows: int = 300,
                     progress: ProgressFn | None = None) -> Table:
    data = table.data_rows()
    headers = table.headers
    if not data or not headers:
        return table
    todo = data[:max_rows]
    batch = 25
    for start in range(0, len(todo), batch):
        chunk = todo[start:start + batch]
        rows = [r.cells for r in chunk]
        prompt = (
            "Correggi gli errori OCR nelle righe di questo listino. "
            "Mantieni esattamente lo stesso numero di righe, colonne e valori "
            "numerici/codici. Inserisci gli spazi mancanti nelle descrizioni. "
            "Restituisci solo JSON.\n"
            f"Intestazioni: {json.dumps(headers, ensure_ascii=False)}\n"
            f"Righe: {json.dumps(rows, ensure_ascii=False)}\n"
            'Formato: {"rows": [["...", "..."], ...]}'
        )
        raw = client.chat(prompt, system=CLEAN_SYSTEM, json_mode=True,
                          max_tokens=4096)
        obj = _extract_json(raw)
        fixed = obj.get("rows") if isinstance(obj, dict) else obj
        if not isinstance(fixed, list) or len(fixed) != len(chunk):
            log.warning("AI clean: invalid row count, keeping original batch")
            continue
        for original, new_cells in zip(chunk, fixed):
            if not isinstance(new_cells, list) or len(new_cells) != len(original.cells):
                continue
            cleaned = []
            for old, new in zip(original.cells, new_cells):
                new = "" if new is None else str(new)
                # never accept a changed number/code-looking value
                if _looks_numeric(old) and _looks_numeric(new):
                    if re.sub(r"[^\d]", "", old) != re.sub(r"[^\d]", "", new):
                        new = old
                cleaned.append(new)
            original.cells = cleaned
        if progress:
            progress(f"AI: pulizia righe {start + len(chunk)}/{len(todo)}",
                     start + len(chunk), len(todo))
    return table


def _looks_numeric(value: str) -> bool:
    v = (value or "").strip()
    return bool(v) and bool(re.search(r"\d", v)) and not re.search(r"[A-Za-zÀ-ÿ]{3,}", v)


def enhance_result(result: ExtractionResult, settings: AISettings,
                   profile: Profile, progress: ProgressFn | None = None,
                   pdf_path: str | None = None) -> ExtractionResult:
    if not settings.enabled:
        return result
    client = AIClient(settings)
    total = len(result.tables)
    for i, table in enumerate(result.tables):
        if settings.map_columns and table.source != "cards":
            if progress:
                progress(f"AI: mappatura colonne ({i + 1}/{total})", i, total)
            try:
                map_table_headers(client, table, progress)
            except AIError as exc:
                result.warnings.append(f"AI mappatura colonne: {exc}")
        if settings.clean_rows and table.source != "ai":
            if progress:
                progress(f"AI: correzione OCR ({i + 1}/{total})", i, total)
            try:
                clean_table_rows(client, table, settings.max_rows, progress)
            except AIError as exc:
                result.warnings.append(f"AI correzione OCR: {exc}")
    if settings.vision_fallback and pdf_path:
        pages = list(result.meta.get("empty_pages") or [])
        if pages:
            try:
                _vision_pages(result, client, profile, pdf_path, pages,
                              settings.max_rows, progress)
            except AIError as exc:
                result.warnings.append(f"AI Vision: {exc}")
    result.meta["ai_used"] = True
    return result


def _vision_pages(result: ExtractionResult, client: AIClient, profile: Profile,
                  pdf_path: str, pages: list[int], max_pages: int,
                  progress: ProgressFn | None) -> None:
    from .pdftext import PdfDocument

    pages = pages[:max(1, min(20, max_pages))]
    with PdfDocument(pdf_path) as doc:
        for n, pno in enumerate(pages):
            if progress:
                progress(f"AI Vision: pagina {pno}", n, len(pages))
            png = doc.render_png(pno - 1, dpi=150)
            prompt = (
                "Trascrivi la tabella prezzi di questa pagina. "
                "Restituisci colonne e righe in JSON. Se non c'è una tabella, "
                'rispondi {"columns": [], "rows": []}. '
                'Formato: {"columns": ["..."], "rows": [["..."]]}'
            )
            raw = client.chat(prompt, system=VISION_SYSTEM, json_mode=True,
                              image_png=png, max_tokens=4096)
            obj = _extract_json(raw)
            columns = obj.get("columns") if isinstance(obj, dict) else None
            rows = obj.get("rows") if isinstance(obj, dict) else None
            if not isinstance(columns, list) or not isinstance(rows, list):
                continue
            if len(columns) < 2 or not rows:
                continue
            cols = []
            for i, c in enumerate(columns):
                name = canonical_header(str(c), profile) or str(c) or f"Colonna {i + 1}"
                cols.append(Column(name=name, x0=0, x1=0, original=str(c)))
            body: list[Row] = []
            for r in rows:
                if not isinstance(r, list) or not any(str(x).strip() for x in r):
                    continue
                cells = [str(x) if x is not None else "" for x in r]
                cells = (cells + [""] * len(cols))[:len(cols)]
                body.append(Row(cells, pno, kind="data"))
            if not body:
                continue
            for ci, col in enumerate(cols):
                col.kind = column_kind(col.name, [r.cells[ci] for r in body])
            title = f"AI Vision pagina {pno}"
            result.tables.append(Table(columns=cols, rows=body, pages=[pno],
                                       title=title, profile=profile.id,
                                       source="ai",
                                       warnings=["Tabella estratta con AI Vision: verificare."]))
    result.merge_similar()
