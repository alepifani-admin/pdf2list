"""PDF text/word extraction with a built-in cross-platform OCR fallback."""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path
from typing import Callable

import fitz  # PyMuPDF

from .models import Band, PageText, Word

log = logging.getLogger(__name__)


class PdfError(RuntimeError):
    pass


class PdfPasswordRequired(PdfError):
    pass


class OcrError(RuntimeError):
    pass


ProgressFn = Callable[[str, int, int], None]


def _luminance(color) -> float:
    if color is None:
        return 1.0
    if isinstance(color, (int, float)):
        return float(color)
    if isinstance(color, (list, tuple)):
        if len(color) >= 3:
            return (float(color[0]) + float(color[1]) + float(color[2])) / 3.0
        if len(color) == 1:
            return float(color[0])
    return 1.0


class OcrEngine:
    """Lazy wrapper around RapidOCR (ONNX, CPU, works offline)."""

    _lock = threading.Lock()
    _instance: "OcrEngine | None" = None

    def __init__(self) -> None:
        self._engine = None
        self._load_error: str | None = None

    @classmethod
    def instance(cls) -> "OcrEngine":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _ensure(self):
        if self._engine is not None:
            return self._engine
        if self._load_error:
            raise OcrError(self._load_error)
        try:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
            log.info("OCR engine ready (RapidOCR)")
            return self._engine
        except Exception as exc:  # pragma: no cover - depends on install
            self._load_error = (
                "Motore OCR non disponibile. Installa rapidocr-onnxruntime."
                f" Dettaglio: {exc}"
            )
            raise OcrError(self._load_error) from exc

    def recognize(self, rgb: "object", width: int, height: int) -> list[tuple]:
        """Run OCR on an RGB numpy array; returns (text, x0, y0, x1, y1, conf)."""
        engine = self._ensure()
        try:
            # RapidOCR expects BGR for numpy input.
            bgr = rgb[:, :, ::-1]
            result, _ = engine(bgr)
        except Exception as exc:  # pragma: no cover
            raise OcrError(f"OCR non riuscito: {exc}") from exc
        out: list[tuple] = []
        for item in result or []:
            if len(item) < 3:
                continue
            box, text, conf = item[0], str(item[1]), float(item[2])
            if not text.strip():
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            x0, x1 = max(0.0, min(xs)), min(float(width), max(xs))
            y0, y1 = max(0.0, min(ys)), min(float(height), max(ys))
            if x1 <= x0 or y1 <= y0:
                continue
            out.append((text, x0, y0, x1, y1, conf))
        return out


def _char_weight(ch: str) -> float:
    if ch.isspace():
        return 0.32
    if ch in "il.,:;'|!`":
        return 0.3
    if ch in "mwMW@%":
        return 0.8
    if ch.isupper():
        return 0.62
    return 0.5


def split_ocr_tokens(text: str, x0: float, x1: float) -> list[tuple[str, float, float]]:
    """Split an OCR line box into words with estimated horizontal positions."""
    tokens = [t for t in text.split() if t]
    if len(tokens) <= 1:
        t = text.strip()
        return [(t, x0, x1)] if t else []
    weights = []
    pos = 0.0
    for t in tokens:
        w = sum(_char_weight(c) for c in t)
        weights.append((t, pos, pos + w))
        pos += w + 0.32
    total = pos - 0.32 if pos else 1.0
    span = max(0.0, x1 - x0)
    out = []
    for t, a, b in weights:
        ta = x0 + span * (a / total)
        tb = x0 + span * (b / total)
        out.append((t, ta, tb))
    return out


class PdfDocument:
    def __init__(self, path: str | Path, password: str | None = None) -> None:
        self.path = str(path)
        try:
            self.doc = fitz.open(self.path)
        except Exception as exc:
            raise PdfError(f"Impossibile aprire il PDF: {exc}") from exc
        if self.doc.needs_pass:
            if not password or not self.doc.authenticate(password):
                self.doc.close()
                raise PdfPasswordRequired(
                    "Il PDF è protetto da password: inserisci la password per continuare."
                )
        if self.doc.page_count <= 0:
            self.doc.close()
            raise PdfError("Il PDF non contiene pagine.")

    @property
    def page_count(self) -> int:
        return self.doc.page_count

    def close(self) -> None:
        try:
            self.doc.close()
        except Exception:
            pass

    def __enter__(self) -> "PdfDocument":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def page_size(self, index: int) -> tuple[float, float]:
        rect = self.doc[index].rect
        return rect.width, rect.height

    def render_png(self, index: int, dpi: int = 150) -> bytes:
        page = self.doc[index]
        pix = page.get_pixmap(dpi=dpi, alpha=False, colorspace=fitz.csRGB)
        return pix.tobytes("png")

    def _bands(self, page) -> list[Band]:
        bands: list[Band] = []
        try:
            drawings = page.get_drawings()
        except Exception:
            return bands
        pw, ph = page.rect.width, page.rect.height
        for d in drawings:
            try:
                r = d.get("rect")
                if r is None:
                    continue
                x0, y0 = float(r.x0), float(r.y0)
                x1, y1 = float(r.x1), float(r.y1)
                w, h = x1 - x0, y1 - y0
                if w < 18 or h < 4 or h > 70:
                    continue
                if w >= pw * 0.94 and h >= ph * 0.94:
                    continue
                fill = d.get("fill")
                if fill is None:
                    continue
                lum = _luminance(fill)
                if lum >= 0.995:
                    continue
                bands.append(Band(x0, y0, x1, y1, dark=lum < 0.6))
            except Exception:
                continue
        return bands

    def _hlines(self, page) -> list[tuple[float, float, float]]:
        out: list[tuple[float, float, float]] = []
        try:
            drawings = page.get_drawings()
        except Exception:
            return out
        pw = page.rect.width
        for d in drawings:
            try:
                r = d.get("rect")
                if r is None:
                    continue
                x0, y0, x1, y1 = float(r.x0), float(r.y0), float(r.x1), float(r.y1)
                w, h = x1 - x0, y1 - y0
                if h > 2.5 or w < pw * 0.18:
                    continue
                if w >= pw * 0.97 and (y0 < 4 or y1 > page.rect.height - 4):
                    continue
                out.append((y0, x0, x1))
            except Exception:
                continue
        return out

    def extract_page(
        self,
        index: int,
        *,
        ocr_mode: str = "auto",
        dpi: int = 200,
        progress: ProgressFn | None = None,
    ) -> PageText:
        page = self.doc[index]
        width, height = page.rect.width, page.rect.height
        words: list[Word] = []
        has_text = False
        if ocr_mode != "always":
            try:
                raw = page.get_text("words")
            except Exception:
                raw = []
            for item in raw:
                if len(item) < 5:
                    continue
                text = str(item[4]).strip()
                if not text:
                    continue
                x0, y0, x1, y1 = (float(item[0]), float(item[1]),
                                  float(item[2]), float(item[3]))
                words.append(Word(text, x0, y0, x1, y1, 1.0))
            chars = sum(len(w.text) for w in words if any(c.isalnum() for c in w.text))
            has_text = len(words) >= 5 and chars >= 30
        ocr_used = False
        need_ocr = ocr_mode == "always" or (ocr_mode == "auto" and not has_text)
        if need_ocr:
            if progress:
                progress(f"OCR pagina {index + 1}", index, self.page_count)
            try:
                import numpy as np

                pix = page.get_pixmap(dpi=dpi, alpha=False, colorspace=fitz.csRGB)
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, pix.n
                )
                raw = OcrEngine.instance().recognize(arr, pix.width, pix.height)
                sx = width / max(1, pix.width)
                sy = height / max(1, pix.height)
                words = []
                for line_no, (text, x0, y0, x1, y1, conf) in enumerate(raw):
                    for tok, tx0, tx1 in split_ocr_tokens(text, x0, x1):
                        words.append(
                            Word(
                                tok,
                                tx0 * sx,
                                y0 * sy,
                                tx1 * sx,
                                y1 * sy,
                                conf,
                                line_id=line_no,
                            )
                        )
                ocr_used = True
                has_text = bool(words)
            except OcrError:
                raise
            except Exception as exc:
                raise OcrError(f"OCR della pagina {index + 1} non riuscito: {exc}") from exc
        return PageText(
            number=index + 1,
            width=width,
            height=height,
            words=words,
            has_text_layer=has_text and not ocr_used,
            ocr_used=ocr_used,
            bands=self._bands(page),
            hlines=self._hlines(page),
            rotation=int(getattr(page, "rotation", 0) or 0),
        )
