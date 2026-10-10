"""Eksport specyfikacji OpenAPI aplikacji (tryb `cloud`) do `docs/openapi.json`.

Bez uruchamiania serwera i bez dotykania bazy: aplikacja jest tylko budowana (`create_app`), a dokument bierzemy
z `app.openapi()`. Wynik jest deterministyczny (posortowane klucze, stale ustawienia niezalezne od srodowiska).

    python tools/export_openapi.py            # zapisuje docs/openapi.json
    python tools/export_openapi.py --check    # kod 1, gdy plik w repo jest nieaktualny (CI)
    python tools/export_openapi.py -o -       # na stdout

Kokpit generuje z tego pliku typy (`openapi-typescript`); po zmianie API trzeba go odswiezyc i zacommitowac.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # uruchomienie jako `python tools/export_openapi.py` z dowolnego katalogu
    sys.path.insert(0, str(ROOT))

DEFAULT_OUTPUT = ROOT / "docs" / "openapi.json"
#: Sekret tylko po to, by `create_app` przeszedl kontrole startowa trybu cloud; nigdy nie trafia do dokumentu.
_PLACEHOLDER_SECRET = "openapi-export-placeholder-secret-0123456789"  # noqa: S105


def build_document() -> dict:
    """Dokument OpenAPI aplikacji w trybie `cloud` (ustawienia jawne, niezalezne od zmiennych srodowiska)."""
    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(
        _env_file=None, daas_mode="cloud", app_env="prod", daas_secret_key=_PLACEHOLDER_SECRET,
        daas_api_key=None, daas_cockpit_origin=None, daas_metrics_token=None,
        daas_control_db_url="sqlite+aiosqlite:///:memory:", daas_auto_migrate=False)
    return create_app(settings).openapi()


def render() -> str:
    """Tekst pliku: JSON z wcieciem 2, posortowanymi kluczami i znakiem konca linii."""
    return json.dumps(build_document(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUTPUT), help="plik wyjsciowy albo `-` (stdout)")
    parser.add_argument("--check", action="store_true", help="nie zapisuj; kod 1, gdy plik jest nieaktualny")
    args = parser.parse_args(argv)
    text = render()
    if args.output == "-":
        sys.stdout.write(text)
        return 0
    target = Path(args.output)
    if args.check:
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if current != text:
            print(f"{target} is out of date: run `python tools/export_openapi.py`", file=sys.stderr)
            return 1
        print(f"{target} is up to date")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {target} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
