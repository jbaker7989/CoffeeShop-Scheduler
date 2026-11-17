"""Explicit access-token revocation primitives (CS-061b)."""
from __future__ import annotations
import threading
from datetime import datetime


class RevocationList:
    def __init__(self) -> None:
        self._jtis: set[str] = set()
        self._lock = threading.RLock()
    def revoke(self, jti: str) -> None:
        with self._lock: self._jtis.add(jti)
    def is_revoked(self, jti: str) -> bool:
        with self._lock: return jti in self._jtis
