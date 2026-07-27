from __future__ import annotations

from dataclasses import dataclass
import keyring
import keyring.errors

from config.app_settings import CONFIG_FILE, load_config_data, remove_setting, save_config_data

APP_NAME = "PLR000-CCA001 Extractor"
USERNAME_KEY = "username"
PASSWORD_KEY = "lexis_password"


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


def save_credentials(username: str, password: str) -> None:
    """
    Persist username (plaintext) and password (secure keyring).
    """
    config_data = load_config_data()
    config_data[USERNAME_KEY] = username
    save_config_data(config_data)

    keyring.set_password(APP_NAME, PASSWORD_KEY, password)


def load_credentials() -> Credentials | None:
    """
    Load credentials if both username and password exist.
    """
    if not CONFIG_FILE.exists():
        return None

    try:
        data = load_config_data()
        username = data.get(USERNAME_KEY)
        password = keyring.get_password(APP_NAME, PASSWORD_KEY)

        if not username or not password:
            return None

        return Credentials(username=username, password=password)

    except Exception:
        # Corrupt config or keyring issue
        return None


def clear_credentials() -> None:
    """
    Remove stored credentials completely.
    """
    remove_setting(USERNAME_KEY)

    try:
        keyring.delete_password(APP_NAME, PASSWORD_KEY)
    except keyring.errors.PasswordDeleteError:
        pass
