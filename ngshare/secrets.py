import base64
import logging
import os
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

HKDF_INFO = b"ngshare-course-settings-v1"


def derive_fernet_key(service_token: str | bytes) -> bytes:
    if isinstance(service_token, str):
        token_bytes = service_token.encode('utf-8')
    else:
        token_bytes = service_token

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=HKDF_INFO,
    )
    derived = hkdf.derive(token_bytes)
    return base64.urlsafe_b64encode(derived)


def get_fernet(key_material: str | bytes | None = None) -> Fernet:
    if key_material is None:
        key_material = os.environ.get('NGSHARE_SECRET_KEY') or os.environ.get(
            'JUPYTERHUB_API_TOKEN', ''
        )
    if not key_material:
        raise RuntimeError(
            'Encryption key material not configured: set NGSHARE_SECRET_KEY or JUPYTERHUB_API_TOKEN'
        )
    key = derive_fernet_key(key_material)
    return Fernet(key)


def encrypt(plaintext: str | None, key_material: str | bytes | None = None) -> str | None:
    if not plaintext:
        return None
    f = get_fernet(key_material)
    return f.encrypt(plaintext.encode('utf-8')).decode('utf-8')


def decrypt(ciphertext: str | None, key_material: str | bytes | None = None) -> str | None:
    if not ciphertext:
        return None
    f = get_fernet(key_material)
    try:
        return f.decrypt(ciphertext.encode('utf-8')).decode('utf-8')
    except (InvalidToken, Exception):
        logger.warning(
            "Failed to decrypt git token (possibly rotated key or invalid ciphertext)"
        )
        return None


def get_token_status(ciphertext: str | None, key_material: str | bytes | None = None) -> str:
    if not ciphertext:
        return "none"
    decrypted = decrypt(ciphertext, key_material)
    if decrypted is not None:
        return "ok"
    return "unreadable"
