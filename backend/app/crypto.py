"""Encryption of the model API keys that the Models page saves.

The key comes from TRIALIQ_SECRET_KEY, or from backend/data/secret.key, which the app makes the first time and
which only the owner can read. Without it the saved keys cannot be read and must be entered again."""

import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"
_lock = threading.Lock()
_fernets: dict[str, Fernet] = {}


def _cipher(data_dir: Path) -> Fernet:
    with _lock:
        key = os.environ.get("TRIALIQ_SECRET_KEY", "").strip()
        if not key:
            path = Path(data_dir) / "secret.key"
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(Fernet.generate_key())
                os.chmod(path, 0o600)
            key = path.read_text().strip()
        if key not in _fernets:
            _fernets[key] = Fernet(key.encode())
        return _fernets[key]


def encrypt(value: str, data_dir: Path) -> str:
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + _cipher(data_dir).encrypt(value.encode()).decode()


def decrypt(value: str, data_dir: Path) -> str:
    if not value or not value.startswith(PREFIX):
        return value
    try:
        return _cipher(data_dir).decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("A saved key cannot be decrypted: the secret key changed. Enter the key again.") from exc
