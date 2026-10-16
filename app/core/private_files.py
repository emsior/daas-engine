"""Pliki prywatne (tylko wlasciciel) niezaleznie od systemu.

POSIX: tryb `0600` przy tworzeniu. Windows: bity trybu nie maja znaczenia (NTFS uzywa DACL), wiec po utworzeniu
pliku lista dostepu jest zawezana przez `icacls` do biezacego konta (`/inheritance:r /grant:r USER:(R,W)`).
Uzywane dla plikow, ktore niosa sekrety (np. outbox poczty `console` z linkami logowania).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_ICACLS_TIMEOUT_SEC = 15


def current_account() -> str | None:
    """Konto, ktore ma zostac jedynym uprawnionym (Windows: `DOMENA\\uzytkownik`, lokalnie `KOMPUTER\\uzytkownik`)."""
    user = os.environ.get("USERNAME")
    if not user:
        return None
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{user}" if domain else user


def restrict_to_owner(path: Path) -> bool:
    """Zawezenie DACL pliku do biezacego konta (Windows). Na POSIX nic nie robi i zwraca True.

    Zwraca False, gdy nie da sie tego wykonac (brak `icacls`, brak nazwy konta, blad narzedzia) — wywolujacy
    decyduje, czy to blad krytyczny. Nic z tresci pliku nie trafia do logu.
    """
    if os.name != "nt":
        return True
    icacls = shutil.which("icacls")
    account = current_account()
    if icacls is None or account is None:
        log.warning("private file: cannot restrict ACL (icacls=%s, account=%s)", bool(icacls), bool(account))
        return False
    try:
        done = subprocess.run(  # noqa: S603  stale argumenty, bez powloki
            [icacls, str(path), "/inheritance:r", "/grant:r", f"{account}:(R,W)"],
            capture_output=True, timeout=_ICACLS_TIMEOUT_SEC, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("private file: icacls failed (%s)", type(exc).__name__)
        return False
    if done.returncode != 0:
        log.warning("private file: icacls exit %s", done.returncode)
        return False
    return True


def open_private(path: Path, flags: int = os.O_WRONLY | os.O_CREAT | os.O_EXCL) -> int:
    """Tworzy plik dostepny tylko dla wlasciciela i zwraca deskryptor (jak `os.open`).

    Gdy na Windows nie da sie zawezic DACL, plik jest usuwany i leci `PermissionError` — lepiej odmowic,
    niz zostawic sekret w pliku czytelnym dla innych kont.
    """
    fd = os.open(path, flags, 0o600)
    if os.name == "nt" and not restrict_to_owner(path):
        os.close(fd)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        raise PermissionError(f"cannot restrict access to {os.path.basename(path)}")
    return fd
