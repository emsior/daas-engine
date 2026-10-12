"""`tools/lock_windows_pins.py`: wpisy z markerem w lockach (bez sieci: skroty podstawione w tescie)."""
from __future__ import annotations

from tools.lock_windows_pins import DEV_LOCK, MAIN_LOCK, PINS, Pin, apply_pins, split_blocks

LOCK = """#
# header
#
alpha==1.0 \\
    --hash=sha256:aa
    # via daas-engine (pyproject.toml)
uvloop==0.23.0 \\
    --hash=sha256:bb
    # via uvicorn
zeta==2.0 \\
    --hash=sha256:cc
    # via alpha

# The following packages are considered to be unsafe in a requirements file:
setuptools==84.0.0 \\
    --hash=sha256:dd
"""


def fake_hashes(name: str, version: str) -> list[str]:
    return [f"{name}-{version}-2", f"{name}-{version}-1"]


def test_adds_marker_to_existing_entry_and_inserts_new_one_in_order():
    pins = [Pin("uvloop", 'sys_platform != "win32"'), Pin("pywin32", 'sys_platform == "win32"', "312", "mcp (Windows only)")]
    out = apply_pins(LOCK, pins, fake_hashes)
    lines = out.splitlines()
    assert 'uvloop==0.23.0 ; sys_platform != "win32" \\' in lines
    assert "    # via uvicorn" in lines  # komentarz pip-compile zostaje
    names = [b[0].split("==")[0] for b in split_blocks(out)[1]]
    assert names == ["alpha", "pywin32", "uvloop", "zeta"]
    block = lines[lines.index('pywin32==312 ; sys_platform == "win32" \\'):][:4]
    assert block[1:] == ["    --hash=sha256:pywin32-312-1 \\", "    --hash=sha256:pywin32-312-2",
                         "    # via mcp (Windows only)"]
    assert out.index("pywin32") < out.index("# The following packages")  # nie trafia do sekcji unsafe
    assert "setuptools==84.0.0" in out


def test_is_idempotent():
    pins = [Pin("uvloop", 'sys_platform != "win32"'), Pin("tzdata", 'sys_platform == "win32"', "2026.5", "pandas")]
    once = apply_pins(LOCK, pins, fake_hashes)
    assert apply_pins(once, pins, fake_hashes) == once


def test_repository_locks_already_carry_the_platform_pins():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for lock in (MAIN_LOCK, DEV_LOCK):
        text = (root / lock).read_text(encoding="utf-8")
        for pin in PINS:
            if lock in pin.locks:
                assert f"\n{pin.name}==" in text and f" ; {pin.marker} \\" in text, (lock, pin.name)
