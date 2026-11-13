"""Field-level encryption for secrets stored in the database.

AES-256-GCM with a key ring. Every ciphertext records the id of the key that
produced it, so keys can be rotated without downtime. The caller supplies
"associated data" naming the row and purpose (for example
"user:<id>:totp"); a ciphertext copied to another row or column then fails to
decrypt, which stops one user's secret being swapped for another's.
"""
import base64
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

_PREFIX = "enc"
_NONCE_BYTES = 12


class DecryptionError(Exception):
    pass


class FieldCipher:
    def __init__(self, keys: dict[str, bytes], active_key_id: str):
        if active_key_id not in keys:
            raise ValueError("active key is not in the key ring")
        self._keys = keys
        self._active = active_key_id

    def encrypt(self, plaintext: str, associated_data: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = AESGCM(self._keys[self._active]).encrypt(nonce, plaintext.encode(), associated_data.encode())
        return f"{_PREFIX}:{self._active}:{base64.urlsafe_b64encode(nonce + sealed).decode()}"

    def decrypt(self, token: str, associated_data: str) -> str:
        try:
            prefix, key_id, encoded = token.split(":", 2)
            raw = base64.urlsafe_b64decode(encoded)
        except ValueError as exc:
            raise DecryptionError("malformed ciphertext") from exc
        if prefix != _PREFIX or key_id not in self._keys or len(raw) <= _NONCE_BYTES:
            raise DecryptionError("unknown key or malformed ciphertext")
        try:
            plain = AESGCM(self._keys[key_id]).decrypt(
                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], associated_data.encode()
            )
        except InvalidTag as exc:
            raise DecryptionError("ciphertext failed authentication") from exc
        return plain.decode()

    def needs_rotation(self, token: str) -> bool:
        """True when the value was encrypted with a key other than the active one."""
        parts = token.split(":", 2)
        return len(parts) != 3 or parts[1] != self._active


@lru_cache
def get_cipher() -> FieldCipher:
    settings = get_settings()
    return FieldCipher(settings.encryption_keys(), settings.data_encryption_active_key)


def generate_key() -> str:
    """A fresh base64 key for DATA_ENCRYPTION_KEYS."""
    return base64.b64encode(os.urandom(32)).decode()
