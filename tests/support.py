"""Pomocniki wspolne dla testow (importowane jawnie: `from tests.support import ...`)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Zmienne srodowiska, bez ktorych interpreter/narzedzia nie startuja; reszta jest celowo odcinana
#: (test ma dowiesc, ze podproces nie polega na innych zmiennych DAAS_*).
_OS_ESSENTIAL_ENV = ("PATH", "HOME") if os.name != "nt" else (
    "PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP",
    "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "USERNAME", "USERDOMAIN")


def minimal_subprocess_env(home: Path, **extra: str) -> dict[str, str]:
    """Srodowisko dla podprocesu: tylko zmienne systemowe + `extra`; `HOME`/`USERPROFILE` wskazuje `home`."""
    env = {k: os.environ[k] for k in _OS_ESSENTIAL_ENV if k in os.environ}
    env.setdefault("PATH", "/usr/bin:/bin" if os.name != "nt" else os.environ.get("PATH", ""))
    exe_dir = str(Path(sys.executable).parent)
    if exe_dir not in env["PATH"].split(os.pathsep):
        env["PATH"] = exe_dir + os.pathsep + env["PATH"]
    env["HOME"] = str(home)
    if os.name == "nt":
        env["USERPROFILE"] = str(home)
    env.update(extra)
    return env


def assert_private_file(path: Path) -> None:
    """Plik czyta i zapisuje tylko wlasciciel: POSIX = tryb 0600, Windows = DACL z jednym wpisem (R,W)."""
    assert path.is_file()
    if os.name != "nt":
        assert oct(path.stat().st_mode & 0o777) == "0o600"
        return
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True, check=True).stdout  # noqa: S603, S607
    aces = [line for line in out.splitlines() if ":(" in line]
    assert len(aces) == 1 and "(R,W)" in aces[0], out
