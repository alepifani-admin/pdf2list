"""User settings, API keys and platform paths."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .ai import AISettings

log = logging.getLogger(__name__)

APP_DIR_NAME = "PDF2LIST"
KEYRING_SERVICE = "PDF2LIST"

ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "custom": "PDF2LIST_API_KEY",
}


def app_data_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = base / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


@dataclass
class ExportSettings:
    csv_delimiter: str = ";"
    decimal_comma: bool = True
    include_page_column: bool = True
    one_sheet_per_table: bool = True
    combined_sheet: bool = True
    open_after_export: bool = True
    currency_symbol: str = "€"
    freeze_header: bool = True
    auto_filter: bool = True


@dataclass
class AppSettings:
    ocr_mode: str = "auto"
    dpi: int = 200
    profile_id: str = "generic"
    custom_profile_path: str = ""
    ai_enabled: bool = False
    provider: str = "openai"
    base_url: str = ""
    model: str = ""
    ai_map_columns: bool = True
    ai_clean_rows: bool = False
    ai_vision_fallback: bool = False
    ai_max_rows: int = 300
    ai_timeout: int = 90
    last_dir: str = ""
    window_width: int = 1180
    window_height: int = 780
    export: ExportSettings = field(default_factory=ExportSettings)

    def ai_settings(self) -> AISettings:
        return AISettings(
            enabled=self.ai_enabled,
            provider=self.provider,
            base_url=self.base_url,
            model=self.model,
            api_key=get_api_key(self.provider),
            timeout=self.ai_timeout,
            map_columns=self.ai_map_columns,
            clean_rows=self.ai_clean_rows,
            vision_fallback=self.ai_vision_fallback,
            max_rows=self.ai_max_rows,
        )


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "si", "sì", "on"}:
            return True
        if v in {"0", "false", "no", "off", ""}:
            return False
    return default


def _as_int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _as_str(value, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _sanitize_app(key: str, value, default):
    if key == "ocr_mode":
        return value if value in {"auto", "always", "never"} else default
    if key == "dpi":
        return _as_int(value, int(default), 72, 600)
    if key in {"ai_enabled", "ai_map_columns", "ai_clean_rows", "ai_vision_fallback"}:
        return _as_bool(value, bool(default))
    if key == "ai_max_rows":
        return _as_int(value, int(default), 10, 3000)
    if key == "ai_timeout":
        return _as_int(value, int(default), 10, 300)
    if key == "window_width":
        return _as_int(value, int(default), 800, 4000)
    if key == "window_height":
        return _as_int(value, int(default), 600, 3000)
    if key in {"profile_id", "provider"}:
        return _as_str(value, str(default)).strip() or str(default)
    if key in {"custom_profile_path", "base_url", "model", "last_dir"}:
        return _as_str(value, str(default)).strip()
    return value


def _sanitize_export(key: str, value, default):
    if key == "csv_delimiter":
        text = _as_str(value, str(default))
        return text if text in {";", ",", "\t"} else str(default)
    if key in {"decimal_comma", "include_page_column", "one_sheet_per_table",
               "combined_sheet", "open_after_export", "freeze_header",
               "auto_filter"}:
        return _as_bool(value, bool(default))
    if key == "currency_symbol":
        return _as_str(value, str(default)).strip()[:3] or str(default)
    return value


def load_settings() -> AppSettings:
    path = settings_path()
    if not path.is_file():
        return AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("cannot read settings: %s", exc)
        return AppSettings()
    if not isinstance(data, dict):
        return AppSettings()
    export = ExportSettings()
    raw_export = data.pop("export", None)
    if isinstance(raw_export, dict):
        for k, v in raw_export.items():
            if hasattr(export, k):
                setattr(export, k, _sanitize_export(k, v, getattr(export, k)))
    settings = AppSettings(export=export)
    for k, v in data.items():
        if hasattr(settings, k) and k != "export":
            setattr(settings, k, _sanitize_app(k, v, getattr(settings, k)))
    return settings


def save_settings(settings: AppSettings) -> None:
    path = settings_path()
    data = asdict(settings)
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".settings-", suffix=".tmp",
                                        dir=str(path.parent))
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(path)
        tmp_path = None
    except Exception as exc:
        log.error("cannot save settings: %s", exc)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _keyring():
    try:
        import keyring

        return keyring
    except Exception:
        return None


def get_api_key(provider: str) -> str:
    env_name = ENV_KEYS.get(provider)
    if env_name and os.environ.get(env_name):
        return str(os.environ[env_name])
    if os.environ.get("PDF2LIST_API_KEY"):
        return str(os.environ["PDF2LIST_API_KEY"])
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(KEYRING_SERVICE, provider)
            if value:
                return value
        except Exception as exc:
            log.debug("keyring read failed: %s", exc)
    return ""


def set_api_key(provider: str, value: str) -> tuple[bool, str]:
    """Store a key in the OS keychain. Returns (ok, message)."""
    kr = _keyring()
    if kr is None:
        return False, ("Portachiavi di sistema non disponibile: la chiave resterà "
                       "solo in memoria per questa sessione.")
    try:
        if value:
            kr.set_password(KEYRING_SERVICE, provider, value)
        else:
            try:
                kr.delete_password(KEYRING_SERVICE, provider)
            except Exception:
                pass
        return True, "Chiave salvata nel portachiavi di sistema."
    except Exception as exc:
        log.warning("keyring write failed: %s", exc)
        return False, f"Impossibile salvare nel portachiavi: {exc}"


def configure_logging(level: int = logging.INFO) -> Path:
    path = log_dir() / "pdf2list.log"
    # Rotate before opening the handler: renaming an open file fails on
    # Windows and keeps writing to the old inode on POSIX.
    try:
        if path.is_file() and path.stat().st_size > 1_500_000:
            backup = path.with_suffix(".1")
            backup.unlink(missing_ok=True)
            path.replace(backup)
    except OSError as exc:
        log.debug("cannot rotate log: %s", exc)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    # avoid duplicate handlers if configure_logging is called more than once
    for existing in list(root.handlers):
        if not isinstance(existing, logging.FileHandler):
            continue
        try:
            same = Path(existing.baseFilename).resolve() == path.resolve()
        except OSError:
            same = False
        if same:
            root.removeHandler(existing)
            existing.close()
    root.addHandler(handler)
    return path
