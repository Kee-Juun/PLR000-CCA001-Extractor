from __future__ import annotations

from dataclasses import dataclass

try:
    import keyring
    import keyring.errors as keyring_errors
except ModuleNotFoundError:
    keyring = None
    keyring_errors = None

from config.app_settings import CONFIG_FILE, load_config_data, remove_setting, save_config_data

APP_NAME = "PLR000-CCA001 Extractor"
USERNAME_KEY = "username"
PASSWORD_KEY = "lexis_password"


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


def is_keyring_available() -> bool:
    """Return True when the OS keyring package is available in this runtime."""
    return keyring is not None


def save_credentials(username: str, password: str) -> bool:
    """
    Persist the username and, when available, store the password in the OS keyring.

    Returns True when the password was saved to the keyring, else False.
    """
    config_data = load_config_data()
    config_data[USERNAME_KEY] = username
    save_config_data(config_data)

    if keyring is None:
        return False

    try:
        keyring.set_password(APP_NAME, PASSWORD_KEY, password)
        return True
    except Exception:
        return False


def load_credentials() -> Credentials | None:
    """
    Load the saved username and, when available, the saved password.
    """
    if not CONFIG_FILE.exists():
        return None

    try:
        data = load_config_data()
        username = data.get(USERNAME_KEY)
        if not username:
            return None

        password = ""
        if keyring is not None:
            try:
                password = keyring.get_password(APP_NAME, PASSWORD_KEY) or ""
            except Exception:
                password = ""

        return Credentials(username=username, password=password)

    except Exception:
        # Corrupt config or keyring issue
        return None


def clear_credentials() -> None:
    """
    Remove stored credentials completely.
    """
    remove_setting(USERNAME_KEY)

    if keyring is None or keyring_errors is None:
        return

    try:
        keyring.delete_password(APP_NAME, PASSWORD_KEY)
    except keyring_errors.PasswordDeleteError:
        pass
