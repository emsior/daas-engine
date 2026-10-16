"""Wpisy zaleznosci zaleznych od platformy w lockach (`requirements.lock`, `requirements-dev.lock`).

`pip-compile` rozwiazuje zaleznosci dla platformy, na ktorej dziala (Linux), i nie przenosi markerow srodowiska:
pakiety tylko-Windows (`tzdata` dla pandas, `colorama` dla pytest, `pywin32` dla `mcp`) w ogole nie trafiaja do locka,
a `uvloop` (tylko poza Windows) trafia bez markera, przez co `pip install --require-hashes` na Windows sie wywraca.
Ten skrypt naprawia to powtarzalnie: dla kazdego wpisu z `PINS` (albo z `--pin`) wstawia lub odswieza blok

    <pakiet>==<wersja> ; <marker> \\
        --hash=sha256:...   (wszystkie pliki wydania z PyPI, posortowane jak u pip-compile)
        # via ...

w porzadku alfabetycznym, przed sekcja "unsafe". Skrot pobiera z `https://pypi.org/pypi/<pakiet>/<wersja>/json`
(tylko stdlib: `urllib`). Wersja: `--pin` > `PINS` > wersja juz zapisana w locku (np. `uvloop` po pip-compile).
Uruchamiany jest bez zmian wielokrotnie (idempotentny).

Kolejnosc pracy przy zmianie zaleznosci (z korzenia repo, Python 3.11 = minimum projektu, jak w naglowku locka):

    pip-compile --all-build-deps --allow-unsafe --generate-hashes --output-file=requirements.lock --strip-extras pyproject.toml
    pip-compile --all-build-deps --allow-unsafe --extra=dev --generate-hashes --output-file=requirements-dev.lock --strip-extras pyproject.toml
    python tools/lock_windows_pins.py            # dopisuje/odswieza wpisy z markerami w obu lockach
    python tools/lock_windows_pins.py --check    # kod 1, gdy locki wymagaja odswiezenia (bez zapisu)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPI_URL = "https://pypi.org/pypi/{name}/{version}/json"
UNSAFE_HEADER = "# The following packages are considered to be unsafe in a requirements file:"
MAIN_LOCK = "requirements.lock"
DEV_LOCK = "requirements-dev.lock"
WIN32 = 'sys_platform == "win32"'
NOT_WIN32 = 'sys_platform != "win32"'


@dataclass(frozen=True)
class Pin:
    """Wpis z markerem. `version=None` = wersja juz obecna w locku; `via=None` = zostaw komentarz z locka."""

    name: str
    marker: str
    version: str | None = None
    via: str | None = None
    locks: tuple[str, ...] = (MAIN_LOCK, DEV_LOCK)


#: Jedyne zrodlo prawdy o wpisach zaleznych od platformy. Nowa zaleznosc tylko-Windows = nowy wiersz.
PINS: tuple[Pin, ...] = (
    Pin("uvloop", NOT_WIN32),                                        # z uvicorn[standard]; brak kol na Windows
    Pin("tzdata", WIN32, "2026.5", "pandas (Windows only)"),
    Pin("pywin32", WIN32, "312", "mcp (Windows only)"),
    Pin("colorama", WIN32, "0.4.6", "pytest (Windows only)", locks=(DEV_LOCK,)),
)

_REQ_LINE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)")


def canonical(name: str) -> str:
    """Nazwa wg PEP 503 (porownania nazw pakietow)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def fetch_hashes(name: str, version: str) -> list[str]:
    """SHA-256 wszystkich plikow wydania (wheel'e i sdist) z PyPI JSON API."""
    url = PYPI_URL.format(name=name, version=version)
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - staly adres https PyPI
        data = json.load(response)
    hashes = sorted({f["digests"]["sha256"] for f in data.get("urls", []) if f.get("digests", {}).get("sha256")})
    if not hashes:
        raise RuntimeError(f"PyPI has no files for {name}=={version}")
    return hashes


def split_blocks(text: str) -> tuple[list[str], list[list[str]], list[str]]:
    """Dzieli lock na: naglowek (komentarze), bloki pakietow (sekcja glowna) i ogon (sekcja "unsafe")."""
    lines = text.splitlines()
    try:
        cut = lines.index(UNSAFE_HEADER)
    except ValueError:
        cut = len(lines)
    body, tail = lines[:cut], lines[cut:]
    head: list[str] = []
    blocks: list[list[str]] = []
    for line in body:
        if _REQ_LINE.match(line):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
        else:
            head.append(line)
    return head, blocks, tail


def _requirement(line: str) -> re.Match[str]:
    match = _REQ_LINE.match(line)
    if match is None:
        raise ValueError(f"not a pinned requirement line: {line[:60]!r}")
    return match


def _block_name(block: list[str]) -> str:
    return canonical(_requirement(block[0]).group("name"))


def render_block(name: str, version: str, marker: str, hashes: list[str], comments: list[str]) -> list[str]:
    hashes = sorted(hashes)  # kolejnosc jak u pip-compile: wynik nie zalezy od kolejnosci plikow w PyPI
    lines = [f"{name}=={version} ; {marker} \\"]
    lines += [f"    --hash=sha256:{h} \\" for h in hashes[:-1]]
    lines.append(f"    --hash=sha256:{hashes[-1]}")
    return lines + comments


def apply_pins(text: str, pins: list[Pin], hashes_of: Callable[[str, str], list[str]]) -> str:
    """Zwraca tekst locka z wstawionymi/odswiezonymi wpisami `pins` (pozostale bloki bez zmian)."""
    head, blocks, tail = split_blocks(text)
    by_name = {_block_name(b): b for b in blocks}
    for pin in pins:
        key = canonical(pin.name)
        existing = by_name.get(key)
        version = pin.version
        if version is None:
            if existing is None:
                raise SystemExit(f"{pin.name}: no version in PINS and no entry in the lock to take it from")
            version = _requirement(existing[0]).group("version")
        old_comments = [line for line in (existing or []) if line.lstrip().startswith("#")]
        comments = [f"    # via {pin.via}"] if pin.via else old_comments or ["    # via (platform-specific pin)"]
        by_name[key] = render_block(pin.name, version, pin.marker, hashes_of(pin.name, version), comments)
    ordered = [by_name[k] for k in sorted(by_name)]
    out = head + [line for block in ordered for line in block] + tail
    return "\n".join(out) + "\n"


def parse_overrides(values: list[str]) -> dict[str, str]:
    overrides = {}
    for value in values:
        name, sep, version = value.partition("==")
        if not sep or not name or not version:
            raise SystemExit(f"--pin expects NAME==VERSION, got {value!r}")
        overrides[canonical(name)] = version
    return overrides


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(ROOT), help="katalog z plikami lock (domyslnie korzen repo)")
    parser.add_argument("--pin", action="append", default=[], metavar="NAME==VERSION",
                        help="nadpisuje wersje wpisu z PINS (mozna podac wiele razy)")
    parser.add_argument("--check", action="store_true", help="nie zapisuj; kod 1, gdy lock wymaga zmian")
    args = parser.parse_args(argv)
    overrides = parse_overrides(args.pin)
    unknown = sorted(set(overrides) - {canonical(p.name) for p in PINS})
    if unknown:
        raise SystemExit(f"--pin for a package outside PINS: {', '.join(unknown)} (add it to PINS first)")
    pins = [replace(p, version=overrides.get(canonical(p.name), p.version)) for p in PINS]
    cache: dict[tuple[str, str], list[str]] = {}

    def hashes_of(name: str, version: str) -> list[str]:
        if (name, version) not in cache:
            cache[(name, version)] = fetch_hashes(name, version)
        return cache[(name, version)]

    stale = 0
    for lock in (MAIN_LOCK, DEV_LOCK):
        path = Path(args.root) / lock
        current = path.read_text(encoding="utf-8")
        updated = apply_pins(current, [p for p in pins if lock in p.locks], hashes_of)
        if updated == current:
            print(f"{lock}: up to date")
            continue
        stale += 1
        if args.check:
            print(f"{lock}: platform pins out of date: run `python tools/lock_windows_pins.py`", file=sys.stderr)
        else:
            path.write_text(updated, encoding="utf-8", newline="\n")
            print(f"{lock}: updated")
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
