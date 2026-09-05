"""
Encryption for stored health data.

Health markers are the most sensitive thing this system holds — more so than
allergies, which the user already chooses to share with the community module.
A database dump or a stray backup must not expose someone's HbA1c.

Three properties this module enforces:

  1. **Fail closed.** With no HEALTH_ENCRYPTION_KEY configured, encryption is
     unavailable and every caller is expected to refuse the operation rather
     than fall back to plaintext. A forgotten config value must not silently
     downgrade to storing blood test results in the clear.

  2. **Whole-payload encryption.** The entire marker set is encrypted as one
     JSON blob, not field by field. Encrypting only the numeric value would
     leave `analyte = "hba1c", flag = "high"` readable in the clear, which is
     the diagnosis — the part that actually matters.

  3. **No queryability.** Because the payload is opaque, health data can only
     be read by loading a specific user's own rows. There is deliberately no
     way to query "all users with elevated glucose": the schema itself refuses
     to answer that question.
"""

import json
from typing import Any, Optional

from app.core.config import get_settings

settings = get_settings()

_fernet = None
_init_error: Optional[str] = None


class HealthEncryptionUnavailable(Exception):
    """No usable encryption key, so health data cannot be read or written."""


def _get_fernet():
    """Build the cipher once, or record why it could not be built."""
    global _fernet, _init_error

    if _fernet is not None:
        return _fernet
    if _init_error is not None:
        raise HealthEncryptionUnavailable(_init_error)

    key = (settings.HEALTH_ENCRYPTION_KEY or "").strip()
    if not key:
        _init_error = (
            "HEALTH_ENCRYPTION_KEY is not set. Health documents are disabled "
            "rather than stored unencrypted."
        )
        raise HealthEncryptionUnavailable(_init_error)

    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:  # noqa: BLE001 — malformed key, wrong length, bad base64
        _init_error = (
            f"HEALTH_ENCRYPTION_KEY is not a valid Fernet key ({e}). Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
        raise HealthEncryptionUnavailable(_init_error) from e

    return _fernet


def is_available() -> bool:
    """Whether health features can operate. Never raises."""
    try:
        _get_fernet()
        return True
    except HealthEncryptionUnavailable:
        return False


def unavailable_reason() -> Optional[str]:
    """Why health features are off, for logs and operator-facing errors."""
    if is_available():
        return None
    return _init_error


def encrypt_json(payload: Any) -> str:
    """Serialise and encrypt. Raises HealthEncryptionUnavailable with no key."""
    token = _get_fernet().encrypt(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    return token.decode("ascii")


def decrypt_json(token: str) -> Any:
    """
    Decrypt and deserialise.

    A row that cannot be decrypted — written under a rotated or different key —
    raises rather than returning a partial result. Health data that we cannot
    prove we read correctly must not reach the scoring engine.
    """
    raw = _get_fernet().decrypt(token.encode("ascii") if isinstance(token, str) else token)
    return json.loads(raw.decode("utf-8"))
