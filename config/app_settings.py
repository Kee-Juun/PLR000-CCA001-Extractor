from __future__ import annotations

from pathlib import Path
from typing import Any
import json


CONFIG_DIR = Path.home() / ".plr000_cca001_extractor"
CONFIG_FILE = CONFIG_DIR / "config.json"
HEADER_FILL_COLOR_KEY = "header_fill_color"
RECIPIENT_OVERRIDE_TO_KEY = "recipient_override_to"
RECIPIENT_OVERRIDE_CC_KEY = "recipient_override_cc"
SOURCE_MODE_KEY = "source_mode"


def load_config_data() -> dict[str, Any]:
    """Load the shared JSON config file, returning an empty config on failure."""
    if not CONFIG_FILE.exists():
        return {}

    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

    return data if isinstance(data, dict) else {}


def save_config_data(data: dict[str, Any]) -> None:
    """Persist the shared JSON config file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_setting(key: str, default: Any = None) -> Any:
    """Return one saved setting value."""
    return load_config_data().get(key, default)


def save_setting(key: str, value: Any) -> None:
    """Save one setting value without overwriting unrelated config keys."""
    config_data = load_config_data()
    config_data[key] = value
    save_config_data(config_data)


def remove_setting(key: str) -> None:
    """Remove one setting value while preserving the rest of the config."""
    config_data = load_config_data()
    if key not in config_data:
        return

    del config_data[key]
    if config_data:
        save_config_data(config_data)
    elif CONFIG_FILE.exists():
        CONFIG_FILE.unlink()
