"""Kontrola przyjmowania plikow klienta dla POST /upload.

Model wdrozenia: SINGLE-TENANT PER DEPLOYMENT.
Jeden klient = jedna instancja = jeden runtime root. Nie ma tu pojecia
tenant_id ani routingu miedzy najemcami; granica jest granica procesu.

Modul jest celowo oddzielony od warstwy HTTP: cala walidacja daje sie
przetestowac bez podnoszenia FastAPI, a handler endpointu zostaje krotki.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- limity
MAX_UPLOAD_BYTES = 10_485_760          # 10 MiB
MAX_FILENAME_LEN = 128                 # znakow oryginalnej nazwy
MAX_RECORDS = 100_000                  # wierszy danych (bez naglowka)
MAX_CSV_FIELD_BYTES = 65_536           # 64 KiB na pojedyncze pole
CHUNK_SIZE = 65_536                    # 64 KiB na iteracje odczytu
ALLOWED_SUFFIX = ".csv"                # rozszerzenie pliku zapisanego na dysku (XLSX jest konwertowany do CSV)
ALLOWED_UPLOAD_SUFFIXES = (".csv", ".xlsx")  # rozszerzenia przyjmowane od klienta

#: Kolejnosc prob dekodowania. Bez heurystyk i bez errors="replace" —
#: plik albo dekoduje sie jednym z tych trzech, albo jest odrzucany.
ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "cp1250")

#: Minimalny kontrakt kolumn po zmapowaniu aliasow.
REQUIRED_CANON_COLUMNS: tuple[str, ...] = ("revenue", "date")


class ReasonCode:
    """Stabilne kody odmowy. Trafiaja do audytu i do logow klienta.

    Wartosci sa czescia kontraktu — zmiana lamie integracje po stronie klienta.
    """

    OK = "ok"
    MULTIPLE_FILES = "multiple_files"
    EMPTY_FILENAME = "empty_filename"
    FILENAME_TOO_LONG = "filename_too_long"
    FILENAME_ILLEGAL_CHARS = "filename_illegal_chars"
    EXTENSION_NOT_ALLOWED = "extension_not_allowed"
    FILE_TOO_LARGE = "file_too_large"
    FILE_EMPTY = "file_empty"
    DECODE_FAILED = "decode_failed"
    HEADER_MISSING = "header_missing"
    SCHEMA_MISMATCH = "schema_mismatch"
    TOO_MANY_RECORDS = "too_many_records"
    FIELD_TOO_LARGE = "field_too_large"
    PATH_ESCAPE = "path_escape"
    POLICY_DENIED = "policy_denied"
    PARSER_ERROR = "parser_error"
    INTERNAL_ERROR = "internal_error"
    # uwierzytelnianie i uruchomienia (zdarzenia audytowe poza uploadem)
    AUTH_MISSING = "auth_missing"
    AUTH_INVALID = "auth_invalid"
    AUTH_NOT_CONFIGURED = "auth_not_configured"
    RUN_FAILED = "run_failed"
    QUOTA_EXCEEDED = "quota_exceeded"
    # import XLSX (app.core.xlsx_import)
    XLSX_INVALID = "xlsx_invalid"
    XLSX_MACROS = "xlsx_macros_not_allowed"
    XLSX_UNPACKED_TOO_LARGE = "xlsx_unpacked_too_large"
    XLSX_MULTIPLE_SHEETS = "xlsx_multiple_sheets"
    XLSX_TOO_MANY_SHEETS = "xlsx_too_many_sheets"
    XLSX_SHEET_NOT_FOUND = "xlsx_sheet_not_found"
    XLSX_TOO_WIDE = "xlsx_too_wide"


class UploadRejected(Exception):
    """Odmowa przyjecia pliku. Niesie kod powodu i kod HTTP.

    Komunikat jest celowo ubogi: nie zawiera sciezek, nazw ani fragmentow
    tresci pliku. Szczegoly diagnostyczne zostaja po stronie serwera.
    """

    def __init__(self, reason: str, http_status: int, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.http_status = http_status
        self.message = message


# --------------------------------------------------------------------------- nazwa
#: Znaki, ktore w nazwie od klienta konczą sie odmowa (fail-closed).
_ILLEGAL_IN_FILENAME = ("..", "/", "\\", "\x00")


def validate_original_filename(name: str | None) -> str:
    """Sprawdza nazwe przyslana przez klienta.

    Nazwa NIE buduje sciezki docelowej — sluzy wylacznie do kontroli rozszerzenia
    i do odrzucenia oczywistych prob manipulacji. Docelowa nazwe generuje serwer.
    """
    if name is None or not name.strip():
        raise UploadRejected(ReasonCode.EMPTY_FILENAME, 400, "brak nazwy pliku")

    if len(name) > MAX_FILENAME_LEN:
        raise UploadRejected(
            ReasonCode.FILENAME_TOO_LONG, 400,
            f"nazwa pliku dluzsza niz {MAX_FILENAME_LEN} znakow",
        )

    for bad in _ILLEGAL_IN_FILENAME:
        if bad in name:
            raise UploadRejected(
                ReasonCode.FILENAME_ILLEGAL_CHARS, 400,
                "nazwa pliku zawiera niedozwolone znaki",
            )

    if not name.lower().endswith(ALLOWED_UPLOAD_SUFFIXES):
        raise UploadRejected(
            ReasonCode.EXTENSION_NOT_ALLOWED, 415,
            "dozwolone sa wylacznie rozszerzenia .csv i .xlsx",
        )

    return name


def is_xlsx_name(name: str) -> bool:
    """True dla nazwy z rozszerzeniem .xlsx (bez wzgledu na wielkosc liter). Wywolywac po walidacji nazwy."""
    return name.lower().endswith(".xlsx")


# --------------------------------------------------------------------------- sciezki
def ensure_inside_root(candidate: Path, root: Path) -> Path:
    """Kontrola przynaleznosci sciezki do runtime root tego wdrozenia.

    Swiadomie NIE uzywamy str.startswith() — porownanie tekstowe przepuszcza
    katalog o wspolnym prefiksie nazwy (np. 'runtime-backup' wobec 'runtime').
    Path.is_relative_to() porownuje komponenty sciezki, nie znaki.
    """
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise UploadRejected(ReasonCode.PATH_ESCAPE, 400, "sciezka poza obszarem roboczym")
    return resolved


def new_upload_id() -> str:
    return uuid.uuid4().hex


def temp_path_for(upload_id: str, root: Path) -> Path:
    """Plik tymczasowy lezy w tym samym runtime root co plik finalny.

    Ten sam katalog = ten sam filesystem = os.replace() jest atomowy
    i nie przechodzi przez kopiowanie miedzy wolumenami.
    """
    return root / f"{upload_id}.tmp"


def final_path_for(upload_id: str, root: Path) -> Path:
    return root / f"{upload_id}{ALLOWED_SUFFIX}"


#: Wzorzec nazwy pliku tymczasowego tworzonego przez `temp_path_for` (obce pliki *.tmp nie sa ruszane).
_TMP_NAME = re.compile(r"^[0-9a-f]{32}\.tmp$")

#: Po tylu sekundach plik *.tmp po przerwanym uploadzie (np. zabity proces) uznajemy za porzucony.
STALE_TMP_AGE_SEC = 900.0


def cleanup_stale_tmp(root: Path, max_age_sec: float = STALE_TMP_AGE_SEC, now: float | None = None) -> int:
    """Usuwa porzucone pliki `<uuid>.tmp` starsze niz `max_age_sec` (0 = wszystkie). Zwraca liczbe usunietych.

    Normalna sciezka uploadu sprząta sama (blok finally w handlerze); ta funkcja zbiera to, co zostaje po
    twardym przerwaniu procesu. Brak katalogu nie jest bledem.
    """
    if not root.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - max_age_sec
    removed = 0
    for entry in root.iterdir():
        if not entry.is_file() or not _TMP_NAME.match(entry.name):
            continue
        try:
            if max_age_sec <= 0 or entry.stat().st_mtime <= cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            continue  # plik zniknal miedzy listowaniem a usunieciem (rownolegly upload) — nie jest bledem
    return removed


def dir_usage_bytes(root: Path) -> int:
    """Suma rozmiarow plikow w katalogu uploadow (finalne i trwajace tymczasowe)."""
    if not root.is_dir():
        return 0
    total = 0
    for entry in root.iterdir():
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


# --------------------------------------------------------------------------- dekodowanie
def decode_strict(raw: bytes) -> tuple[str, str]:
    """Dekoduje bajty jednym z dozwolonych kodowan albo odrzuca plik.

    Zwraca (tekst, nazwa_kodowania). Brak errors='replace' i brak heurystyk:
    cicha podmiana bajtow na U+FFFD zamienilaby uszkodzony plik w dane
    wygladajace poprawnie, a to gorsze niz odmowa.
    """
    # BOM rozstrzygamy jawnie: utf-8-sig dekoduje takze pliki bez BOM, wiec
    # probowany jako pierwszy przypisalby etykiete "utf-8-sig" kazdemu plikowi
    # UTF-8. W audycie chcemy wiedziec, co naprawde przyszlo.
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            return raw.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            pass

    for enc in ("utf-8", "cp1250"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue

    raise UploadRejected(
        ReasonCode.DECODE_FAILED, 422,
        "nie udalo sie odczytac pliku w obslugiwanym kodowaniu (UTF-8 lub CP1250)",
    )


# --------------------------------------------------------------------------- CSV
def _sniff_delimiter(sample: str) -> str:
    """Wykrywa separator sposrod przecinka, srednika i tabulatora.

    Polskie eksporty uzywaja srednika rownie czesto co przecinka, wiec bez
    tego kroku odrzucalibysmy poprawne pliki klientow.
    """
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in (",", ";", "\t")}
        best = max(counts, key=lambda k: counts[k])
        return best if counts[best] > 0 else ","


def iter_validated_rows(text: str, max_records: int = MAX_RECORDS) -> Iterator[list[str]]:
    """Przechodzi CSV wiersz po wierszu, pilnujac limitow.

    Iteracyjnie, bez budowania listy wszystkich wierszy — inaczej limit
    100k rekordow bylby egzekwowany dopiero po zmaterializowaniu calosci
    w pamieci, czyli za pozno.
    """
    csv.field_size_limit(MAX_CSV_FIELD_BYTES)

    sample = text[:8192]
    delimiter = _sniff_delimiter(sample)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    try:
        header = next(reader)
    except StopIteration:
        raise UploadRejected(ReasonCode.HEADER_MISSING, 422, "plik nie zawiera naglowka") from None
    except csv.Error as exc:
        if "field larger than field limit" in str(exc):
            raise UploadRejected(
                ReasonCode.FIELD_TOO_LARGE, 422,
                f"pojedyncze pole przekracza {MAX_CSV_FIELD_BYTES} bajtow",
            ) from None
        raise UploadRejected(ReasonCode.PARSER_ERROR, 422, "nie udalo sie odczytac pliku CSV") from None

    if not header or all(not str(c).strip() for c in header):
        raise UploadRejected(ReasonCode.HEADER_MISSING, 422, "plik nie zawiera naglowka")

    yield header

    count = 0
    while True:
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            if "field larger than field limit" in str(exc):
                raise UploadRejected(
                    ReasonCode.FIELD_TOO_LARGE, 422,
                    f"pojedyncze pole przekracza {MAX_CSV_FIELD_BYTES} bajtow",
                ) from None
            raise UploadRejected(ReasonCode.PARSER_ERROR, 422, "nie udalo sie odczytac pliku CSV") from None

        if not row or all(not str(c).strip() for c in row):
            continue

        count += 1
        if count > max_records:
            raise UploadRejected(
                ReasonCode.TOO_MANY_RECORDS, 413,
                f"plik zawiera wiecej niz {max_records} rekordow",
            )
        yield row


@dataclass
class CsvValidationResult:
    encoding: str
    header: list[str]
    record_count: int


def validate_csv_bytes(raw: bytes, *, max_records: int = MAX_RECORDS) -> CsvValidationResult:
    """Pelna walidacja zawartosci przed finalizacja zapisu.

    Kolejnosc ma znaczenie: dekodowanie -> naglowek -> limity -> schemat.
    Kazdy krok konczy sie odmowa z wlasnym reason code. `max_records` to limit wierszy danych
    (CSV: `MAX_RECORDS`; CSV skonwertowany z XLSX: `xlsx_import.MAX_XLSX_ROWS`).
    """
    if not raw.strip():
        raise UploadRejected(ReasonCode.FILE_EMPTY, 400, "plik jest pusty")

    text, encoding = decode_strict(raw)

    rows = iter_validated_rows(text, max_records)
    header = next(rows)
    count = sum(1 for _ in rows)

    _assert_schema(header)

    return CsvValidationResult(encoding=encoding, header=[str(c) for c in header], record_count=count)


def _assert_schema(header: list[str]) -> None:
    """Sprawdza, czy naglowek daje sie zmapowac na wymagane kolumny kanoniczne.

    Korzysta z tego samego mappera co pipeline, zeby kontrakt uploadu
    i kontrakt przetwarzania nie rozjechaly sie w czasie.
    """
    from app.sources.csv_mapper import detect_mapping

    mapping, _ = detect_mapping([str(c).strip() for c in header])
    missing = [c for c in REQUIRED_CANON_COLUMNS if c not in mapping]
    if missing:
        # Bez wypisywania naglowkow klienta — to tresc jego pliku.
        raise UploadRejected(
            ReasonCode.SCHEMA_MISMATCH, 422,
            "plik nie zawiera wymaganych kolumn (przychod, data)",
        )


# --------------------------------------------------------------------------- audyt
#: Dozwolone wartosci pola `action` w zdarzeniu audytowym.
AUDIT_ACTIONS: frozenset[str] = frozenset({"upload", "run", "auth_denied"})


def build_audit_event(
    *,
    allowed: bool,
    reason: str,
    deployment_id: str,
    size_bytes: int = 0,
    action: str = "upload",
    upload_id: str | None = None,
    encoding: str | None = None,
    content_sha256: str | None = None,
    pipeline: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    client_ip: str | None = None,
    route: str | None = None,
) -> dict[str, Any]:
    """Buduje zdarzenie audytowe o scisle ograniczonym zestawie pol (`AUDIT_ALLOWED_KEYS`).

    Swiadomie NIE przyjmuje nazwy pliku od klienta, sciezki, nazwy klienta ani podanego klucza API —
    funkcja nie ma jak ich wyciec, bo ich nie widzi. To mocniejsza gwarancja niz filtrowanie na wyjsciu.
    `action` ∈ AUDIT_ACTIONS: `upload`, `run`, `auth_denied`.
    """
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"unknown audit action: {action!r}")
    event: dict[str, Any] = {
        "event_id": uuid.uuid4().hex,
        "ts": round(time.time(), 3),
        "deployment_id": deployment_id,
        "action": action,
        "decision": "allow" if allowed else "deny",
        "reason_code": reason,
        "size_bytes": size_bytes,
    }
    optional = {
        "upload_id": upload_id, "encoding": encoding, "content_sha256": content_sha256,
        "pipeline": pipeline, "run_id": run_id, "request_id": request_id,
        "client_ip": client_ip, "route": route,
    }
    event.update({key: value for key, value in optional.items() if value})
    return event


#: Jedyne dopuszczalne klucze zdarzenia audytowego (upload, run, auth_denied).
AUDIT_ALLOWED_KEYS: frozenset[str] = frozenset({
    "event_id", "ts", "deployment_id", "action", "decision",
    "reason_code", "size_bytes", "upload_id", "encoding", "content_sha256",
    "pipeline", "run_id", "request_id", "client_ip", "route",
})


def append_audit(event: dict[str, Any], audit_log: Path) -> None:
    """Dopisuje zdarzenie do logu audytowego (append-only, jedna linia JSON).

    Zapis idzie przez wspolny, thread-safe writer warstwy policy — zdarzenia
    uploadu i zdarzenia policy trafiaja do tego samego pliku tym samym kanalem,
    wiec nie moga sie przeplatac w polowie linii.
    """
    from app.core.policy import append_audit_line

    append_audit_line(audit_log, event)


def safe_deployment_id(runtime_root: Path) -> str:
    """Pseudonim wdrozenia — skrot sciezki runtime root, nie sama sciezka.

    Pozwala odroznic instancje w zagregowanych logach, nie ujawniajac
    ukladu katalogow serwera.
    """
    return hashlib.sha256(str(runtime_root.resolve()).encode("utf-8")).hexdigest()[:16]


def content_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
