"""Fernet encryption utilities — cross-compatible with Node.js fernet package.

Uses FERNET_KEY environment variable (shared between engine + web).
Credentials are encrypted at rest in Postgres (via Next.js) and
decrypted in memory here when running scans/trades.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    """Get Fernet instance from FERNET_KEY env var."""
    key = os.environ.get("FERNET_KEY")
    if not key:
        raise RuntimeError("FERNET_KEY environment variable is required")
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns URL-safe base64 token."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a Fernet token back to plaintext."""
    f = _get_fernet()
    return f.decrypt(token.encode()).decode()


def generate_key() -> str:
    """Generate a new Fernet key. Run once, store in .env files."""
    return Fernet.generate_key().decode()


if __name__ == "__main__":
    # Quick helper: python -m src.crypto → prints a new key
    print("New FERNET_KEY:", generate_key())
