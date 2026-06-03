"""CryptoJS-compatible AES-CBC decryption for FuelCompare.ie API responses."""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def cryptojs_decrypt(encrypted_b64: str, evp_key: str) -> list:
    """Decrypt a CryptoJS AES-CBC base64 payload using EvpKDF key derivation.

    fuelcompare.ie API responses are encrypted with CryptoJS AES using a key
    hardcoded in their station JS bundle. CryptoJS uses a non-standard OpenSSL-compatible
    format: base64("Salted__" + 8-byte-salt + ciphertext), with key+IV derived via
    iterative MD5 (EvpKDF). The key is extracted dynamically by PageAssets.
    """
    raw = base64.b64decode(encrypted_b64)
    # CryptoJS Salted__ format: bytes 0-7 = magic, 8-15 = salt, 16+ = ciphertext
    salt = raw[8:16]
    ciphertext = raw[16:]

    # EvpKDF: chain MD5(prev + evp_key + salt) until we have 48 bytes (32 key + 16 IV)
    d, d_i = b"", b""
    while len(d) < 48:
        d_i = hashlib.md5(d_i + evp_key.encode() + salt).digest()
        d += d_i
    key, iv = d[:32], d[32:48]

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    # Remove PKCS7 padding — last byte is the pad length; AES block size is 16
    pad_len = padded[-1]
    if not (1 <= pad_len <= 16):
        raise ValueError(f"Invalid PKCS7 padding length: {pad_len}")
    return json.loads(padded[:-pad_len])
