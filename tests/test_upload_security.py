"""PR #3 — testy bezpieczeństwa POST /upload (kontrakt MVP, single-tenant per deployment).

Zakres: U01–U16. Wszystkie dane są syntetyczne, generowane w locie.
Bez sieci, bez Dockera, bez sekretów. Każdy scenariusz odmowy sprawdza
dodatkowo, że po sobie nie zostawił ani pliku finalnego, ani tymczasowego.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, reset_settings_cache
from app.core.upload_guard import (
    AUDIT_ALLOWED_KEYS,
    MAX_CSV_FIELD_BYTES,
    MAX_FILENAME_LEN,
    MAX_RECORDS,
    MAX_UPLOAD_BYTES,
    ReasonCode,
    UploadRejected,
    ensure_inside_root,
    validate_csv_bytes,
)
from app.pipelines.runner import PipelineRunner
from app.storage.duckdb_client import DuckDBClient

from tests.test_pipelines import PROJECT_ROOT, client, runner, settings  # noqa: F401  (fixtures)

HEADER = "Data,Kwota,Produkt\n"
ROW = "2026-03-01,100.00,Produkt testowy\n"


# ---------------------------------------------------------------------------
# Pomocnicze
# ---------------------------------------------------------------------------
def csv_bytes(rows: int = 10) -> bytes:
    return (HEADER + ROW * rows).encode("utf-8")


def csv_of_exact_size(target: int) -> bytes:
    """Poprawny CSV o dokładnie zadanej liczbie bajtów.

    Wiersze są celowo długie (~250 B). Przy krótkich wierszach plik 10 MiB
    miałby ~300 tys. rekordów, czyli przekraczałby limit MAX_RECORDS — test
    granicy rozmiaru kończyłby się odmową z zupełnie innego powodu i niczego
    by nie dowodził.
    """
    filler = "z" * 200
    body = HEADER.encode()
    row = f"2026-03-01,100.00,Produkt {filler}\n".encode()
    while len(body) + len(row) <= target:
        body += row
    missing = target - len(body)
    if missing:
        prefix = b"2026-03-01,1.00,"
        tail = prefix + b"x" * max(0, missing - len(prefix))
        body += tail[:missing]
    assert len(body) == target
    return body


def uploads_dir(settings: Settings) -> Path:
    return settings.reports_path.parent / "uploads"


def assert_no_leftovers(settings: Settings) -> None:
    """Po odmowie nie może zostać ani plik finalny, ani tymczasowy."""
    up = uploads_dir(settings)
    if not up.exists():
        return
    assert list(up.glob("*.csv")) == [], "pozostał plik finalny po odmowie"
    assert list(up.glob("*.tmp")) == [], "pozostał plik tymczasowy po odmowie"


def audit_records(settings: Settings) -> list[dict]:
    log = settings.duckdb_file.parent / "audit.jsonl"
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.fixture()
def policy_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Klient z DAAS_POLICY_ENFORCE=true — do U13/U14."""
    from app.main import create_app

    for key in ("FACEIT_API_KEY", "LIQUIPEDIA_USER_AGENT", "APIFY_TOKEN",
                "DISCORD_WEBHOOK_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    reset_settings_cache()
    s = Settings(
        _env_file=None,
        duckdb_path=str(tmp_path / "t.duckdb"),
        reports_dir=str(tmp_path / "reports"),
        fixtures_dir=str(PROJECT_ROOT / "data" / "fixtures"),
        daas_policy_enforce=True,
    )
    s.ensure_dirs()
    r = PipelineRunner(settings=s, db=DuckDBClient(s.duckdb_file))
    app = create_app()
    with TestClient(app) as c:
        app.state.runner = r
        yield c, s
    reset_settings_cache()


# ===========================================================================
# U01 — poprawny CSV 1 KiB
# ===========================================================================
def test_u01_valid_csv_1kib_success(client: TestClient, settings):  # noqa: F811
    payload = csv_of_exact_size(1024)
    r = client.post("/upload", files={"file": ("sprzedaz.csv", payload, "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["ready"] is True
    assert body["rows"] > 0

    # Nazwa docelowa generowana przez serwer: <uuid>.csv
    assert body["filename"].endswith(".csv")
    assert len(Path(body["filename"]).stem) == 32, "oczekiwano uuid4().hex jako nazwy"
    assert "sprzedaz" not in body["filename"], "oryginalna nazwa nie może trafić do nazwy docelowej"

    stored = list(uploads_dir(settings).glob("*.csv"))
    assert len(stored) == 1
    assert list(uploads_dir(settings).glob("*.tmp")) == []


# ===========================================================================
# U02 / U03 — granica rozmiaru
# ===========================================================================
@pytest.mark.slow
def test_u02_exactly_max_size_passes_size_and_content_checks():
    """Plik dokładnie 10 MiB przechodzi kontrolę rozmiaru i walidację treści.

    Świadomie na poziomie funkcji, nie przez TestClient: finalizacja uploadu
    wywołuje `read_client_csv`, czyli pandas z `engine="python"`, który na
    10 MiB liczy minutami. Test przez API mierzyłby wydajność parsera pandas,
    a nie egzekwowanie granicy — a to właśnie granica jest przedmiotem U02.
    Ścieżkę end-to-end pokrywa U01, przekroczenie limitu — U03.
    """
    payload = csv_of_exact_size(MAX_UPLOAD_BYTES)
    assert len(payload) == MAX_UPLOAD_BYTES

    # rozmiar mieści się w limicie
    assert len(payload) <= MAX_UPLOAD_BYTES

    # treść przechodzi pełną walidację kontraktu
    result = validate_csv_bytes(payload)
    assert result.encoding == "utf-8"
    assert 0 < result.record_count <= MAX_RECORDS


@pytest.mark.slow
def test_u03_one_byte_over_limit_rejected(client: TestClient, settings):  # noqa: F811
    payload = csv_of_exact_size(MAX_UPLOAD_BYTES) + b"x"
    r = client.post("/upload", files={"file": ("za_duzy.csv", payload, "text/csv")})
    assert r.status_code == 413, r.text[:300]
    assert_no_leftovers(settings)


# ===========================================================================
# U04 — wiele plików
# ===========================================================================
def test_u04_multiple_files_rejected(client: TestClient, settings):  # noqa: F811
    """Dwa pola `file` w jednym żądaniu.

    FastAPI zwiąże parametr z jednym z nich; kontrakt wymaga, by drugi plik
    nie został po cichu zapisany. Sprawdzamy skutek: najwyżej jeden plik na dysku.
    """
    files = [
        ("file", ("a.csv", csv_bytes(3), "text/csv")),
        ("file", ("b.csv", csv_bytes(3), "text/csv")),
    ]
    r = client.post("/upload", files=files)
    stored = list(uploads_dir(settings).glob("*.csv")) if uploads_dir(settings).exists() else []
    assert len(stored) <= 1, "żądanie z wieloma plikami zapisało więcej niż jeden plik"
    assert r.status_code in (200, 400, 422)
    assert list(uploads_dir(settings).glob("*.tmp")) == [] if uploads_dir(settings).exists() else True


# ===========================================================================
# U05 — niedozwolone rozszerzenia
# ===========================================================================
@pytest.mark.parametrize("name", [
    "dane.xlsx", "dane.xls", "dane.zip", "dane.gz", "dane.tar",
    "dane.pdf", "dane.json", "dane.xml", "dane.txt", "dane.tsv",
    "dane.png", "dane.exe", "dane", "dane.csv.exe",
])
def test_u05_forbidden_extensions_rejected(client: TestClient, settings, name):  # noqa: F811
    r = client.post("/upload", files={"file": (name, csv_bytes(3), "text/csv")})
    assert r.status_code == 415, f"{name}: oczekiwano 415, otrzymano {r.status_code}"
    assert_no_leftovers(settings)


def test_u05b_uppercase_csv_accepted(client: TestClient, settings):  # noqa: F811
    """Rozszerzenie sprawdzane bez względu na wielkość liter (kontrakt, punkt 2)."""
    r = client.post("/upload", files={"file": ("DANE.CSV", csv_bytes(3), "text/csv")})
    assert r.status_code == 200, r.text[:200]


# ===========================================================================
# U06 / U07 — nazwa pliku
# ===========================================================================
@pytest.mark.parametrize("name", [
    "../../../etc/passwd.csv",
    "..\\..\\..\\Windows\\System32\\drivers\\etc\\hosts.csv",
    "....//....//tajne.csv",
    "uploads-evil/payload.csv",
    "katalog/plik.csv",
    "katalog\\plik.csv",
])
def test_u06_traversal_filenames_rejected(client: TestClient, settings, name):  # noqa: F811
    r = client.post("/upload", files={"file": (name, csv_bytes(3), "text/csv")})
    assert r.status_code in (400, 415), f"{name}: oczekiwano odmowy, otrzymano {r.status_code}"

    detail = json.dumps(r.json(), ensure_ascii=False)
    for leak in ("C:\\", "C:/", "/srv/", "uploads", str(settings.reports_path)):
        assert leak not in detail, f"komunikat odmowy ujawnia ścieżkę: {leak}"

    assert_no_leftovers(settings)


def test_u07_empty_and_overlong_filenames_rejected(client: TestClient, settings):  # noqa: F811
    # pusta nazwa
    r_empty = client.post("/upload", files={"file": ("", csv_bytes(3), "text/csv")})
    assert r_empty.status_code in (400, 415, 422)

    # nazwa ponad limit
    long_name = "a" * (MAX_FILENAME_LEN + 1) + ".csv"
    r_long = client.post("/upload", files={"file": (long_name, csv_bytes(3), "text/csv")})
    assert r_long.status_code == 400

    assert_no_leftovers(settings)


def test_u07b_filename_at_limit_accepted(client: TestClient):  # noqa: F811
    """Kontrola pozytywna granicy: dokładnie 128 znaków musi przejść."""
    name = "a" * (MAX_FILENAME_LEN - len(".csv")) + ".csv"
    assert len(name) == MAX_FILENAME_LEN
    r = client.post("/upload", files={"file": (name, csv_bytes(3), "text/csv")})
    assert r.status_code == 200, r.text[:200]


def test_u07c_polish_filename_accepted(client: TestClient):  # noqa: F811
    """Diakrytyki i spacje w nazwie są legalne — nazwa i tak nie buduje ścieżki."""
    for name in ["zamówienia_ąćęłńóśźż.csv", "raport marzec 2026.csv", "dane (kopia).csv"]:
        r = client.post("/upload", files={"file": (name, csv_bytes(3), "text/csv")})
        assert r.status_code == 200, f"{name}: {r.status_code} {r.text[:150]}"


def test_u07d_nul_byte_in_filename_rejected():
    """Bajt NUL sprawdzany jednostkowo — klient HTTP nie przepuści go w nagłówku."""
    from app.core.upload_guard import validate_original_filename

    with pytest.raises(UploadRejected) as exc:
        validate_original_filename("plik\x00.csv")
    assert exc.value.reason == ReasonCode.FILENAME_ILLEGAL_CHARS


# ===========================================================================
# U08 — Content-Type jako sygnał pomocniczy (warunkowy)
# ===========================================================================
def test_u08_content_type_alone_does_not_decide(client: TestClient, settings):  # noqa: F811
    """Content-Type nie może samodzielnie dać allow ani deny.

    Nie udajemy walidacji magic-number — CSV nie ma sygnatury. Sprawdzamy
    dwie strony: poprawny CSV z błędnym Content-Type przechodzi, a plik
    o zabronionym rozszerzeniu nie przechodzi mimo Content-Type text/csv.
    """
    ok = client.post("/upload", files={"file": ("dane.csv", csv_bytes(5), "application/octet-stream")})
    assert ok.status_code == 200, "poprawny CSV odrzucony z powodu Content-Type"

    bad = client.post("/upload", files={"file": ("dane.exe", csv_bytes(5), "text/csv")})
    assert bad.status_code == 415, "rozszerzenie .exe przyjęte na podstawie Content-Type"


# ===========================================================================
# U09 — nagłówek i schemat
# ===========================================================================
def test_u09_missing_header_rejected(client: TestClient, settings):  # noqa: F811
    r = client.post("/upload", files={"file": ("pusty.csv", b"", "text/csv")})
    assert r.status_code in (400, 422)
    assert_no_leftovers(settings)


def test_u09b_schema_mismatch_rejected(client: TestClient, settings):  # noqa: F811
    r = client.post("/upload", files={"file": ("zle.csv", b"aaa,bbb\n1,2\n", "text/csv")})
    assert r.status_code == 422
    assert_no_leftovers(settings)


def test_u09c_error_message_does_not_leak_file_content(client: TestClient):  # noqa: F811
    """Komunikat 422 nie może zawierać nagłówków z pliku klienta."""
    r = client.post("/upload", files={"file": ("zle.csv", b"SEKRETNA_KOLUMNA,INNA\n1,2\n", "text/csv")})
    assert r.status_code == 422
    assert "SEKRETNA_KOLUMNA" not in r.text


# ===========================================================================
# U10 — kodowanie
# ===========================================================================
def test_u10_undecodable_bytes_rejected(client: TestClient, settings):  # noqa: F811
    """Bajty niedekodowalne w UTF-8, UTF-8-SIG ani CP1250 → 422, bez cichej podmiany."""
    payload = HEADER.encode() + b"2026-03-01,100.00,\x81\x8d\x8f\x90\x9d\xfe\xff\n"
    r = client.post("/upload", files={"file": ("zepsuty.csv", payload, "text/csv")})
    assert r.status_code == 422, r.text[:200]
    assert_no_leftovers(settings)


def test_u10b_cp1250_accepted(client: TestClient):  # noqa: F811
    """CP1250 musi przejść — to standardowy eksport polskich systemów ERP."""
    text = "Data,Kwota,Produkt\n2026-03-01,100.00,Zamówienie ąćęłńóśźż\n"
    r = client.post("/upload", files={"file": ("erp.csv", text.encode("cp1250"), "text/csv")})
    assert r.status_code == 200, r.text[:200]


def test_u10c_utf8_sig_accepted(client: TestClient):  # noqa: F811
    text = "Data,Kwota,Produkt\n2026-03-01,100.00,Produkt\n"
    r = client.post("/upload", files={"file": ("bom.csv", text.encode("utf-8-sig"), "text/csv")})
    assert r.status_code == 200, r.text[:200]


# ===========================================================================
# U11 — kolizja nazw
# ===========================================================================
def test_u11_same_filename_twice_distinct_ids(client: TestClient, settings):  # noqa: F811
    r1 = client.post("/upload", files={"file": ("raport.csv", csv_bytes(3), "text/csv")})
    r2 = client.post("/upload", files={"file": ("raport.csv", csv_bytes(4), "text/csv")})
    assert r1.status_code == 200 and r2.status_code == 200

    id1, id2 = r1.json()["upload_id"], r2.json()["upload_id"]
    assert id1 != id2, "dwa uploady tej samej nazwy dostały ten sam identyfikator"
    assert r1.json()["filename"] != r2.json()["filename"]
    assert len(list(uploads_dir(settings).glob("*.csv"))) == 2, "drugi upload nadpisał pierwszy"


# ===========================================================================
# U12 — policy wyłączona (kompatybilność MVP)
# ===========================================================================
def test_u12_policy_disabled_keeps_working(client: TestClient, settings):  # noqa: F811
    """DAAS_POLICY_ENFORCE=false: upload działa, brak nieoczekiwanych odmów."""
    assert settings.daas_policy_enforce is False
    r = client.post("/upload", files={"file": ("dane.csv", csv_bytes(5), "text/csv")})
    assert r.status_code == 200, r.text[:200]

    # Audyt uploadu powstaje niezależnie od flagi — to log operacji na plikach,
    # nie log warstwy policy.
    recs = audit_records(settings)
    assert any(r_.get("action") == "upload" for r_ in recs)


# ===========================================================================
# U13 / U14 — policy włączona
# ===========================================================================
def test_u13_policy_enabled_allows_valid_csv(policy_client):
    c, s = policy_client
    r = c.post("/upload", files={"file": ("dane.csv", csv_bytes(5), "text/csv")})
    assert r.status_code == 200, r.text[:300]

    recs = audit_records(s)
    uploads = [x for x in recs if x.get("action") == "upload"]
    assert uploads and uploads[-1]["decision"] == "allow"
    assert uploads[-1]["reason_code"] == ReasonCode.OK


def test_u14_policy_enabled_denies_and_audits(policy_client):
    """Policy wywołana realnie przez endpoint — nie tylko zaimportowana.

    Podmieniamy granicę workspace na katalog, do którego ścieżka docelowa
    nie należy. Jeśli endpoint faktycznie woła enforce(), upload zostanie
    odrzucony; jeśli policy byłaby tylko importowana, plik przeszedłby.
    """
    c, s = policy_client
    import app.core.policy as policy_mod

    root = s.reports_path.parent / "uploads"

    # Odmowę wymuszamy przez allowlistę ról, a nie przez podmianę walidatora:
    # Pydantic v2 kompiluje walidatory przy tworzeniu klasy, więc podmiana
    # metody po fakcie nie miałaby wpływu na walidację. Zabranie roli `analyst`
    # prawa do `read_file` daje deny w pierwszym kroku enforce() — a to działa
    # tylko wtedy, gdy endpoint naprawdę tę funkcję wywołuje.
    original = policy_mod.ALLOW
    policy_mod.ALLOW = {"researcher": set(), "analyst": set(), "executor": set()}
    try:
        r = c.post("/upload", files={"file": ("dane.csv", csv_bytes(5), "text/csv")})
        assert r.status_code == 403, f"policy nie została wywołana przez endpoint: {r.status_code}"
        assert list(root.glob("*.csv")) == [], "plik finalny powstał mimo odmowy policy"
        assert list(root.glob("*.tmp")) == [], "plik tymczasowy przetrwał odmowę policy"

        recs = [x for x in audit_records(s) if x.get("action") == "upload"]
        assert recs and recs[-1]["decision"] == "deny"
        assert recs[-1]["reason_code"] == ReasonCode.POLICY_DENIED
    finally:
        policy_mod.ALLOW = original


# ===========================================================================
# U15 — higiena audytu
# ===========================================================================
def test_u15_audit_has_no_forbidden_content(client: TestClient, settings):  # noqa: F811
    secret_name = "TAJNA_NAZWA_KLIENTA_2026.csv"
    client.post("/upload", files={"file": (secret_name, csv_bytes(4), "text/csv")})
    client.post("/upload", files={"file": ("zly.xlsx", csv_bytes(2), "text/csv")})

    recs = [r for r in audit_records(settings) if r.get("action") == "upload"]
    assert recs, "brak zdarzeń audytowych uploadu"

    for rec in recs:
        # 1. wyłącznie dozwolone klucze — nowe pole spoza listy zapali ten test
        extra = set(rec.keys()) - AUDIT_ALLOWED_KEYS
        assert not extra, f"niedozwolone pola w audycie: {extra}"

        blob = json.dumps(rec, ensure_ascii=False)
        # 2. brak surowej nazwy pliku od klienta
        assert "TAJNA_NAZWA_KLIENTA" not in blob
        assert ".xlsx" not in blob
        # 3. brak ścieżek
        for leak in ("C:\\", "C:/", "/srv/", "uploads", "Users"):
            assert leak not in blob, f"audyt zawiera fragment ścieżki: {leak}"
        # 4. brak treści pliku
        assert "Produkt testowy" not in blob


def test_u15b_response_has_no_raw_filename(client: TestClient):  # noqa: F811
    r = client.post("/upload", files={"file": ("moja_tajna_nazwa.csv", csv_bytes(3), "text/csv")})
    assert r.status_code == 200
    assert "moja_tajna_nazwa" not in r.json()["filename"]


# ===========================================================================
# U16 — prefix collision i symlink
# ===========================================================================
def test_u16_prefix_collision_denied(tmp_path: Path):
    """Katalog o wspólnym prefiksie nazwy nie należy do runtime root.

    Porównanie tekstowe przez startswith() przepuściłoby 'uploads-evil'
    jako mieszczące się w 'uploads'. Kontrola po komponentach ścieżki nie.
    """
    root = tmp_path / "uploads"
    root.mkdir()
    sibling = tmp_path / "uploads-evil"
    sibling.mkdir()

    with pytest.raises(UploadRejected) as exc:
        ensure_inside_root(sibling / "plik.csv", root)
    assert exc.value.reason == ReasonCode.PATH_ESCAPE

    # kontrola pozytywna — ścieżka wewnątrz musi przejść
    assert ensure_inside_root(root / "ok.csv", root).parent == root.resolve()


def test_u16b_traversal_path_denied(tmp_path: Path):
    root = tmp_path / "uploads"
    root.mkdir()
    with pytest.raises(UploadRejected):
        ensure_inside_root(root / ".." / "poza.csv", root)


def test_u16c_symlink_escape_denied(tmp_path: Path):
    """Dowiązanie wyprowadzające poza root musi zostać odrzucone.

    Na Windows tworzenie dowiązań wymaga uprawnień administratora albo trybu
    dewelopera. Gdy system na to nie pozwala, test jest pomijany z jawnym
    powodem — nie obchodzimy systemu i nie udajemy, że kontrola została
    zweryfikowana.
    """
    root = tmp_path / "uploads"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "tajne.csv").write_text("x", encoding="utf-8")

    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("BLOCKED_OS_PRIVILEGE: brak uprawnień do tworzenia dowiązań")

    with pytest.raises(UploadRejected) as exc:
        ensure_inside_root(link / "tajne.csv", root)
    assert exc.value.reason == ReasonCode.PATH_ESCAPE


# ===========================================================================
# Limity strukturalne CSV — jednostkowo
# ===========================================================================
def test_record_limit_enforced():
    payload = (HEADER + ROW * (MAX_RECORDS + 1)).encode()
    with pytest.raises(UploadRejected) as exc:
        validate_csv_bytes(payload)
    assert exc.value.reason == ReasonCode.TOO_MANY_RECORDS


def test_record_limit_boundary_accepted():
    """Kontrola pozytywna: dokładnie 100 000 rekordów musi przejść."""
    payload = (HEADER + ROW * MAX_RECORDS).encode()
    result = validate_csv_bytes(payload)
    assert result.record_count == MAX_RECORDS


def test_field_size_limit_enforced():
    big = "y" * (MAX_CSV_FIELD_BYTES + 10)
    payload = (HEADER + f"2026-03-01,100.00,{big}\n").encode()
    with pytest.raises(UploadRejected) as exc:
        validate_csv_bytes(payload)
    assert exc.value.reason == ReasonCode.FIELD_TOO_LARGE


def test_field_size_boundary_accepted():
    big = "y" * (MAX_CSV_FIELD_BYTES - 1)
    payload = (HEADER + f"2026-03-01,100.00,{big}\n").encode()
    result = validate_csv_bytes(payload)
    assert result.record_count == 1


# ===========================================================================
# Cleanup po nieoczekiwanym wyjątku
# ===========================================================================
def test_cleanup_after_unexpected_parser_exception(client: TestClient, settings, monkeypatch):  # noqa: F811
    """Nieoczekiwany wyjątek po finalizacji nie może zostawić pliku ani .tmp."""
    import app.sources.csv_mapper as mapper

    def boom(*a, **kw):
        raise RuntimeError("awaria wewnętrzna z /pełną/ścieżką/serwera w treści")

    monkeypatch.setattr(mapper, "map_dataframe", boom)

    r = client.post("/upload", files={"file": ("dane.csv", csv_bytes(5), "text/csv")})
    assert r.status_code == 422
    # komunikat nie może przenosić treści wyjątku
    assert "awaria wewnętrzna" not in r.text
    assert_no_leftovers(settings)
