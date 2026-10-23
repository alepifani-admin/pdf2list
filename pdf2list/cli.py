"""Command-line interface (batch conversion without opening the window)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __app_name__, __version__
from .core.export import (
    configs_path_for,
    export_configs_csv,
    export_csv,
    export_xlsx,
)
from .core.extract import ExtractOptions, extract_pdf
from .core.pdftext import PdfError, PdfPasswordRequired
from .core.profiles import load_profile
from .core.settings import AppSettings, load_settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf2list",
        description="Trasforma un listino PDF in Excel e CSV.",
    )
    p.add_argument("pdf", nargs="?", help="PDF di input (modalità batch)")
    p.add_argument("--pdf", dest="pdf_opt", help="PDF di input")
    p.add_argument("--xlsx", help="Percorso del file Excel di output")
    p.add_argument("--csv", help="Percorso del file CSV di output")
    p.add_argument("--ocr", choices=["auto", "always", "never"], default=None,
                   help="Modalità OCR (default: impostazioni)")
    p.add_argument("--dpi", type=int, default=None, help="DPI OCR (default: impostazioni)")
    p.add_argument("--profile", default=None, help="ID profilo (generic, fernova, ...)")
    p.add_argument("--profile-file", default=None, help="File JSON di un profilo personalizzato")
    p.add_argument("--pages", default=None, help="Intervallo pagine, es. 3-12")
    p.add_argument("--password", default=None, help="Password del PDF")
    p.add_argument("--ai", action="store_true", help="Usa l'AI configurata nelle impostazioni")
    p.add_argument("--no-ai", action="store_true", help="Disabilita l'AI anche se configurata")
    p.add_argument("--quiet", action="store_true", help="Meno messaggi")
    p.add_argument("--test-keyring", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    return p


def _parse_pages(raw: str) -> tuple[int, int | None]:
    text = str(raw).strip().replace(" ", "")
    if not text:
        raise ValueError("empty page range")
    if "-" not in text:
        page = int(text)
        if page < 1:
            raise ValueError("page must be positive")
        return page, page
    first_text, last_text = text.split("-", 1)
    first = int(first_text)
    last = int(last_text) if last_text else None
    if first < 1 or (last is not None and last < first):
        raise ValueError("invalid page range")
    return first, last


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.test_keyring:
        from .core.settings import get_api_key, set_api_key

        ok, msg = set_api_key("diagnostic", "pdf2list-test")
        print(f"set: {ok} {msg}")
        print(f"get: {bool(get_api_key('diagnostic'))}")
        set_api_key("diagnostic", "")
        return 0 if ok else 1
    pdf = args.pdf_opt or args.pdf
    if not pdf:
        build_parser().print_help()
        return 2
    path = Path(pdf)
    if not path.is_file():
        print(f"File non trovato: {path}", file=sys.stderr)
        return 2
    settings: AppSettings = load_settings()
    ocr = args.ocr or settings.ocr_mode
    dpi = int(args.dpi or settings.dpi)
    profile_id = args.profile or settings.profile_id
    profile_path = args.profile_file or (
        settings.custom_profile_path if profile_id == "custom" else None)
    if profile_id == "custom" and not profile_path:
        print("Profilo personalizzato non configurato: usa --profile-file.", file=sys.stderr)
        return 2
    try:
        profile = load_profile(profile_id, profile_path)
    except Exception as exc:
        print(f"Impossibile caricare il profilo: {exc}", file=sys.stderr)
        return 2
    first, last = 1, None
    if args.pages:
        try:
            first, last = _parse_pages(args.pages)
        except (TypeError, ValueError):
            print("Intervallo pagine non valido (usa ad es. 3-12 oppure 5).",
                  file=sys.stderr)
            return 2
    options = ExtractOptions(
        ocr_mode=ocr, dpi=dpi, first_page=first, last_page=last,
        profile_id=profile_id, profile_path=profile_path,
        password=args.password,
    )

    def progress(message: str, current: int, total: int) -> None:
        if not args.quiet:
            print(f"[{current}/{total}] {message}", file=sys.stderr)

    print(f"{__app_name__} {__version__}: {path.name}", file=sys.stderr)
    while True:
        try:
            result = extract_pdf(path, options, profile=profile, progress=progress)
            break
        except PdfPasswordRequired:
            if options.password:
                print("Password non corretta.", file=sys.stderr)
                return 3
            if not sys.stdin.isatty():
                print("Il PDF è protetto da password: usa --password.", file=sys.stderr)
                return 3
            import getpass

            try:
                options.password = getpass.getpass("Password del PDF: ")
            except (EOFError, KeyboardInterrupt):
                return 3
            if not options.password:
                return 3
        except PdfError as exc:
            print(f"Errore PDF: {exc}", file=sys.stderr)
            return 3
        except KeyboardInterrupt:
            print("Operazione annullata.", file=sys.stderr)
            return 130
        except Exception as exc:
            print(f"Errore: {exc}", file=sys.stderr)
            return 1
    if args.ai and not args.no_ai:
        from .core.ai import enhance_result

        ai_settings = settings.ai_settings()
        ai_settings.enabled = True
        try:
            result = enhance_result(result, ai_settings, profile,
                                    progress=progress, pdf_path=str(path))
        except Exception as exc:
            result.warnings.append(f"AI: {exc}")
    if not result.tables:
        print("Nessuna tabella riconosciuta.", file=sys.stderr)
        for w in result.warnings:
            print(f"- {w}", file=sys.stderr)
        return 1
    xlsx = Path(args.xlsx) if args.xlsx else path.with_suffix(".xlsx")
    csv = Path(args.csv) if args.csv else path.with_suffix(".csv")
    try:
        export_xlsx(result, xlsx, settings)
        export_csv(result, csv, settings)
        cfg = export_configs_csv(result, configs_path_for(csv), settings)
    except Exception as exc:
        print(f"Errore durante l'esportazione: {exc}", file=sys.stderr)
        return 1
    print(f"Creato {xlsx} ({result.row_count} righe, {len(result.tables)} tabelle)",
          file=sys.stderr)
    print(f"Creato {csv}", file=sys.stderr)
    if cfg:
        print(f"Creato {cfg}", file=sys.stderr)
    for w in result.warnings[:10]:
        print(f"Avviso: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
