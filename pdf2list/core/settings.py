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


def load_settings() -> AppSettings:
    path = settings_path()
    if not path.is_file():
        return AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("cannot read settings: %s", exc)
        return AppSettings()
    export = ExportSettings()
    for k, v in (data.pop("export", None) or {}).items():
        if hasattr(export, k):
            setattr(export, k, v)
    settings = AppSettings(export=export)
    for k, v in data.items():
        if hasattr(settings, k) and k != "export":
            try:
                setattr(settings, k, v)
            except Exception:
                pass
    return settings


def save_settings(settings: AppSettings) -> None:
    path = settings_path()
    data = asdict(settings)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        log.error("cannot save settings: %s", exc)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
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
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    # keep the log bounded
    try:
        if path.stat().st_size > 1_500_000:
            backup = path.with_suffix(".1")
            path.replace(backup)
    except Exception:
        pass
    return path
