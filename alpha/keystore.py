"""The Gemini key, held only in the Windows Credential Manager."""

from __future__ import annotations

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "ALPHA"
ACCOUNT = "gemini-api"


class KeyStoreError(RuntimeError):
    pass


def save_secret(account: str, value: str) -> None:
    value = value.strip()
    if not value:
        raise KeyStoreError("The key is empty.")
    try:
        keyring.set_password(SERVICE, account, value)
    except KeyringError as error:
        raise KeyStoreError(f"Credential Manager refused the key: {type(error).__name__}") from None


def load_secret(account: str) -> str:
    try:
        return keyring.get_password(SERVICE, account) or ""
    except KeyringError as error:
        raise KeyStoreError(f"Credential Manager could not be read: {type(error).__name__}") from None


def delete_secret(account: str) -> None:
    try:
        keyring.delete_password(SERVICE, account)
    except PasswordDeleteError:
        pass


def save_key(key: str) -> None:
    save_secret(ACCOUNT, key)


def load_key() -> str:
    return load_secret(ACCOUNT)


def has_key() -> bool:
    return bool(load_key())


def delete_key() -> None:
    delete_secret(ACCOUNT)
