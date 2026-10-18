"""Identyfikatory: ULID (sortowalny po czasie) bez zaleznosci zewnetrznych.

ULID = 48 bitow czasu (ms) + 80 bitow losowych, kodowanie Crockford base32, 26 znakow. W obrebie tej samej
milisekundy czesc losowa jest zwiekszana o 1, wiec kolejne identyfikatory z jednego procesu rosna scisle
(klucz paginacji `cursor` w audycie opiera sie na tej wlasnosci).
"""
from __future__ import annotations

import secrets
import threading
import time
import uuid

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_lock = threading.Lock()
_last_ms = -1
_last_rand = 0


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(now_ms: int | None = None) -> str:
    """Zwraca nowy ULID (26 znakow, wielkie litery Crockford base32)."""
    global _last_ms, _last_rand
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    with _lock:
        if ms <= _last_ms:
            ms = _last_ms
            _last_rand += 1
            if _last_rand >= 1 << 80:  # praktycznie nieosiagalne; zaczynamy od nowa w kolejnej ms
                ms += 1
                _last_rand = secrets.randbits(80)
        else:
            _last_rand = secrets.randbits(79)  # zapas na inkrementacje w obrebie ms
        _last_ms = ms
        rand = _last_rand
    return _encode(ms, 10) + _encode(rand, 16)


def new_upload_id() -> str:
    """Identyfikator uploadu = nazwa pliku na dysku (32 znaki hex, jak w trasach legacy)."""
    return uuid.uuid4().hex


def new_run_id() -> str:
    return str(uuid.uuid4())
