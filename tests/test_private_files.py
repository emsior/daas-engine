"""`app.core.private_files`: plik tylko dla wlasciciela na POSIX (0600) i Windows (DACL przez icacls)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.core import private_files as pf
from tests.support import assert_private_file


def test_open_private_creates_an_owner_only_file(tmp_path: Path):
    target = tmp_path / "secret.json"
    fd = pf.open_private(target)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("{}")
    assert_private_file(target)


def test_open_private_refuses_to_overwrite(tmp_path: Path):
    target = tmp_path / "secret.json"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(FileExistsError):
        pf.open_private(target)
    assert target.read_text(encoding="utf-8") == "old"


@pytest.mark.skipif(os.name == "nt", reason="na Windows restrict_to_owner naprawde wola icacls")
def test_restrict_to_owner_is_a_no_op_on_posix(tmp_path: Path):
    target = tmp_path / "f"
    target.write_bytes(b"")
    assert pf.restrict_to_owner(target) is True


@pytest.mark.skipif(os.name != "nt", reason="sciezka Windows")
def test_restrict_to_owner_leaves_a_single_ace(tmp_path: Path):
    target = tmp_path / "f"
    target.write_bytes(b"")
    assert pf.restrict_to_owner(target) is True
    out = subprocess.run(["icacls", str(target)], capture_output=True, text=True, check=True).stdout  # noqa: S603, S607
    assert len([ln for ln in out.splitlines() if ":(" in ln]) == 1


def test_open_private_on_windows_without_icacls_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Gdy DACL nie da sie zawezic, plik z sekretem nie moze zostac na dysku (symulacja Windows bez icacls)."""
    monkeypatch.setattr(pf.os, "name", "nt")
    monkeypatch.setattr(pf.shutil, "which", lambda _name: None)
    target = tmp_path / "secret.json"
    with pytest.raises(PermissionError):
        pf.open_private(target)
    assert not target.exists()


def test_restrict_to_owner_reports_icacls_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pf.os, "name", "nt")
    monkeypatch.setattr(pf.shutil, "which", lambda _name: "C:/Windows/System32/icacls.exe")
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.setenv("USERDOMAIN", "PC")
    calls: list[list[str]] = []

    class _Done:
        returncode = 5

    def fake_run(args, **_kw):
        calls.append(list(args))
        return _Done()

    monkeypatch.setattr(pf.subprocess, "run", fake_run)
    target = tmp_path / "f"
    target.write_bytes(b"")
    assert pf.restrict_to_owner(target) is False
    assert calls and calls[0][1:] == [str(target), "/inheritance:r", "/grant:r", "PC\\tester:(R,W)"]


def test_current_account_prefers_domain_qualified_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.setenv("USERDOMAIN", "PC")
    assert pf.current_account() == "PC\\tester"
    monkeypatch.delenv("USERDOMAIN")
    assert pf.current_account() == "tester"
    monkeypatch.delenv("USERNAME")
    assert pf.current_account() is None
