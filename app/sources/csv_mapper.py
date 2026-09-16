"""Heurystyczny mapper kolumn CSV klienta -> schemat EcommerceOrder.

Klienci przysyłają eksporty z Allegro, Shoper, WooCommerce, Baselinker, PrestaShop,
Excela "po swojemu". Zamiast wymagać idealnego CSV, mapujemy nagłówki po aliasach
(PL/EN), normalizujemy liczby w formacie polskim ("1 299,00 zł") i dopełniamy
brakujące kolumny (profit = revenue - cost, status = completed, category = "Inne").

Mapper NIGDY nie rzuca na brakujących kolumnach opcjonalnych — tylko na braku
minimum: identyfikator zamówienia (lub możliwość jego wygenerowania), data, przychód.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

# kanoniczna kolumna -> lista aliasów (po normalizacji: lowercase, bez ogonków, bez spacji/_/-)
ALIASES: dict[str, list[str]] = {
    "order_id": ["orderid", "id", "orderno", "ordernumber", "nrzamowienia", "numerzamowienia", "zamowienie",
                 "idzamowienia", "numer", "nr", "transactionid", "idtransakcji", "invoice", "faktura", "nrfaktury"],
    "date": ["date", "orderdate", "data", "datazamowienia", "datasprzedazy", "datazakupu", "created", "createdat",
             "datautworzenia", "purchasedate", "datatransakcji", "czas", "time", "timestamp"],
    "product": ["product", "produkt", "nazwaproduktu", "productname", "item", "itemname", "nazwa", "towar",
                "title", "tytul", "sku", "nazwatowaru", "oferta", "offer"],
    "category": ["category", "kategoria", "productcategory", "kategoriaproduktu", "grupa", "group", "typ", "type",
                 "dzial", "branza"],
    "quantity": ["quantity", "qty", "ilosc", "liczba", "sztuk", "szt", "units", "count", "iloscsztuk"],
    "revenue": ["revenue", "przychod", "total", "amount", "kwota", "wartosc", "wartoscbrutto", "cenabrutto",
                "brutto", "kwotabrutto", "sales", "sprzedaz", "cena", "price", "ordertotal", "totalprice",
                "wartosczamowienia", "kwotazamowienia", "razem", "sum", "suma", "netto", "kwotanetto", "wartoscnetto"],
    "cost": ["cost", "koszt", "cogs", "kosztzakupu", "cenazakupu", "purchaseprice", "unitcost", "kosztwlasny",
             "kosztjednostkowy", "buyprice", "zakup"],
    "profit": ["profit", "zysk", "marza", "margin", "marzakwotowa", "dochod", "grossprofit", "zyskbrutto"],
    "discount": ["discount", "rabat", "znizka", "upust", "discountpct", "rabatproc", "promocja", "kupon", "coupon"],
    "status": ["status", "orderstatus", "statuszamowienia", "stan", "state", "paymentstatus", "statusplatnosci"],
}

STATUS_MAP: dict[str, str] = {
    # completed
    "completed": "completed", "complete": "completed", "zrealizowane": "completed", "zrealizowano": "completed",
    "done": "completed", "paid": "completed", "oplacone": "completed", "zaplacone": "completed", "wyslane": "completed",
    "shipped": "completed", "delivered": "completed", "dostarczone": "completed", "ok": "completed",
    "finished": "completed", "zakonczone": "completed", "success": "completed",
    # refunded
    "refunded": "refunded", "refund": "refunded", "zwrot": "refunded", "zwrocone": "refunded", "zwrocono": "refunded",
    "returned": "refunded", "reklamacja": "refunded",
    # cancelled
    "cancelled": "cancelled", "canceled": "cancelled", "anulowane": "cancelled", "anulowano": "cancelled",
    "cancel": "cancelled", "odrzucone": "cancelled", "failed": "cancelled",
    # pending
    "pending": "pending", "oczekujace": "pending", "oczekuje": "pending", "wtrakcie": "pending", "processing": "pending",
    "new": "pending", "nowe": "pending", "unpaid": "pending", "nieoplacone": "pending", "open": "pending",
}


#: Wzorce kompilowane raz. Przy 100k wierszy x kilka kolumn `re.sub` z golym
#: stringiem robi setki tysiecy wejsc do cache'u modulu `re` — to bylo widoczne
#: w profilu jako ~0.9 s czystego narzutu.
_NORM_RE = re.compile(r"[^a-z0-9]")
_NUM_CLEAN_RE = re.compile(r"[^\d,.\-]")

#: Zwykla liczba dziesietna bez smieci i bez przecinka. Dla takiego napisu
#: `float()` daje dokladnie ten sam wynik co pelna sciezka czyszczenia, wiec
#: mozna ominac regex — a to wlasnie regex byl najdrozszy w profilu.
#: Wzorzec celowo NIE dopuszcza wykladnika ani `+`: dla "1e5" stara sciezka
#: zwraca 15.0 (usuwa 'e'), a `float()` 100000.0. Rownowaznosc jest wazniejsza
#: niz dodatkowy zysk.
_PLAIN_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

#: Wartosci tekstowe traktowane jak brak liczby.
_NUM_NULLS = frozenset({"nan", "none", "null", "-"})


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return _NORM_RE.sub("", s.lower())


def _to_number(v: Any) -> float | None:
    """'1 299,00 zł' -> 1299.0 ; '12.5%' -> 12.5 ; '' -> None.

    Wersja skalarna. Pipeline uzywa `to_number_series()`; ta funkcja zostaje jako
    **referencja poprawnosci** — test rownowaznosci porownuje z nia wynik wektorowy.
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.lower() in _NUM_NULLS:
        return None
    if _PLAIN_NUM_RE.fullmatch(s):
        return float(s)
    s = _NUM_CLEAN_RE.sub("", s)
    if not s:
        return None
    if "," in s and "." in s:
        # "1.299,00" (PL) vs "1,299.00" (EN) — decydujemy po ostatnim separatorze
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def to_number_series(col: pd.Series) -> pd.Series:
    """Konwersja calej kolumny na liczby, z wynikiem identycznym jak `_to_number`.

    Uwaga do przyszlych zmian: akcesor `.str` **nie jest** tu szybszy od `.map()`.
    Na tym dtype pandas i tak wchodzi w petle Pythona per komorka
    (`_str_map_str_or_object`), wiec lancuch `.str.replace().str.contains()...`
    zamienia jedno przejscie na kilkanascie. Zmierzone: taki "wektorowy" wariant
    byl ~1.7x wolniejszy od `.map()`. Szybkosc bierze sie z taniego `_to_number`,
    nie z akcesorow.
    """
    # Kolumna juz liczbowa — nic do parsowania.
    if pd.api.types.is_numeric_dtype(col):
        return pd.to_numeric(col, errors="coerce").astype("float64")
    return col.map(_to_number).astype("float64")


def normalize_status_series(col: pd.Series) -> pd.Series:
    """`_norm` + `STATUS_MAP` dla kolumny statusu, liczone raz na unikalna wartosc.

    Status ma znikoma licznosc zbioru wartosci — w eksporcie na 92k wierszy
    wystepuje kilka roznych napisow. Liczenie `_norm` (unicodedata + regex) na
    kazdy wiersz bylo czysta strata: wynik i tak powtarzal sie tysiace razy.
    """
    filled = col.fillna("completed")
    lookup = {v: STATUS_MAP.get(_norm(v), "completed") for v in pd.unique(filled)}
    return filled.map(lookup).astype(object)


#: Formaty dat probowane jawnie, w kolejnosci czestosci w eksportach klientow.
#: Jawny format omija heurystyke `dayfirst`, ktora przy datach ISO generowala
#: UserWarning przy kazdym uruchomieniu i mogla rozjechac dzien z miesiacem.
_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%Y-%m-%d %H:%M:%S",
    "%d.%m.%Y %H:%M",
)


def parse_dates(col: pd.Series) -> pd.Series:
    """Parsuje kolumne daty jawnym formatem, z heurystyka wylacznie jako ostatnia deska.

    Najpierw probuje formatow z `_DATE_FORMATS` — pierwszy, ktory pokryje caly
    niepusty material, wygrywa. Dopiero gdy zaden nie pasuje, wraca do starej
    sciezki `dayfirst=True`, zeby nie odrzucic pliku, ktory wczesniej przechodzil.
    """
    s = col.astype("string").str.strip()
    non_empty = s.notna() & s.ne("")
    target = int(non_empty.sum())

    if target:
        for fmt in _DATE_FORMATS:
            parsed = pd.to_datetime(s, errors="coerce", format=fmt)
            if int(parsed.notna().sum()) == target:
                return parsed

    # Mieszane albo nietypowe formaty — heurystyka pandas, jak dotychczas.
    return pd.to_datetime(s, errors="coerce", dayfirst=True, format="mixed")


@dataclass
class MappingReport:
    mapping: dict[str, str] = field(default_factory=dict)      # canonical -> original header
    unmapped_columns: list[str] = field(default_factory=list)
    generated: list[str] = field(default_factory=list)          # kolumny dopełnione heurystyką
    warnings: list[str] = field(default_factory=list)
    rows_in: int = 0
    rows_out: int = 0
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def detect_mapping(columns: list[str]) -> tuple[dict[str, str], list[str]]:
    """Zwraca (canonical -> original, nieużyte oryginalne kolumny)."""
    normalized = {c: _norm(c) for c in columns}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    # 1) dopasowanie dokładne po aliasie (priorytet wg kolejności aliasów)
    for canon, aliases in ALIASES.items():
        for alias in aliases:
            hit = next((c for c, n in normalized.items() if n == alias and c not in used), None)
            if hit:
                mapping[canon] = hit
                used.add(hit)
                break
    # 2) dopasowanie częściowe (alias zawarty w nagłówku), tylko dla jeszcze nieprzypisanych
    for canon, aliases in ALIASES.items():
        if canon in mapping:
            continue
        for alias in aliases:
            if len(alias) < 4:
                continue
            hit = next((c for c, n in normalized.items() if alias in n and c not in used), None)
            if hit:
                mapping[canon] = hit
                used.add(hit)
                break
    return mapping, [c for c in columns if c not in used]


def map_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, MappingReport]:
    report = MappingReport(rows_in=int(len(df)))
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    mapping, unmapped = detect_mapping(list(df.columns))
    report.mapping, report.unmapped_columns = mapping, unmapped

    out = pd.DataFrame(index=df.index)
    for canon, orig in mapping.items():
        out[canon] = df[orig]

    # --- minimum: revenue + date ---
    if "revenue" not in out.columns:
        raise ValueError(f"Nie znaleziono kolumny przychodu. Nagłówki: {list(df.columns)}")
    if "date" not in out.columns:
        raise ValueError(f"Nie znaleziono kolumny daty. Nagłówki: {list(df.columns)}")

    # --- liczby ---
    for col in ("revenue", "cost", "profit", "discount", "quantity"):
        if col in out.columns:
            out[col] = to_number_series(out[col])

    # --- dopełnienia ---
    if "order_id" not in out.columns:
        out["order_id"] = [f"ROW-{i+1:05d}" for i in range(len(out))]
        report.generated.append("order_id")
    out["order_id"] = out["order_id"].astype(str).str.strip()

    if "quantity" not in out.columns:
        out["quantity"] = 1
        report.generated.append("quantity")
    out["quantity"] = out["quantity"].fillna(1).clip(lower=1).astype(int)

    if "product" not in out.columns:
        out["product"] = "Produkt"
        report.generated.append("product")
    if "category" not in out.columns:
        out["category"] = "Inne"
        report.generated.append("category")
    out["product"] = out["product"].fillna("Produkt").astype(str).str.strip()
    out["category"] = out["category"].fillna("Inne").astype(str).str.strip().replace("", "Inne")

    if "cost" not in out.columns:
        if "profit" in out.columns:
            out["cost"] = out["revenue"] - out["profit"]
            report.generated.append("cost (= revenue - profit)")
        else:
            out["cost"] = 0.0
            report.generated.append("cost (=0, brak danych o koszcie)")
            report.warnings.append("Brak kolumny kosztu — marża = 100%, traktuj zysk jako przychód.")
    out["cost"] = out["cost"].fillna(0.0)

    if "profit" not in out.columns:
        out["profit"] = out["revenue"] - out["cost"]
        report.generated.append("profit (= revenue - cost)")
    out["profit"] = out["profit"].fillna(out["revenue"] - out["cost"])

    if "discount" not in out.columns:
        out["discount"] = 0.0
        report.generated.append("discount (=0)")
    out["discount"] = out["discount"].fillna(0.0)
    out.loc[out["discount"] > 1, "discount"] = out.loc[out["discount"] > 1, "discount"] / 100.0
    out["discount"] = out["discount"].clip(0, 1)

    if "status" not in out.columns:
        out["status"] = "completed"
        report.generated.append("status (=completed)")
    out["status"] = normalize_status_series(out["status"])

    # --- daty ---
    out["date"] = parse_dates(out["date"]).dt.date
    bad_dates = int(out["date"].isna().sum())
    if bad_dates:
        report.warnings.append(f"{bad_dates} wierszy z nieczytelną datą — pominięte.")
        out = out[out["date"].notna()]

    bad_rev = int(out["revenue"].isna().sum())
    if bad_rev:
        report.warnings.append(f"{bad_rev} wierszy bez przychodu — pominięte.")
        out = out[out["revenue"].notna()]
    out = out[out["revenue"] >= 0]

    dup = int(out["order_id"].duplicated().sum())
    if dup:
        report.warnings.append(f"{dup} zduplikowanych order_id — zachowano pierwsze wystąpienie.")
        out = out.drop_duplicates("order_id", keep="first")

    report.rows_out = int(len(out))
    core = ["order_id", "date", "product", "revenue", "cost", "status"]
    report.confidence = round(sum(1 for c in core if c in mapping) / len(core), 2)
    cols = ["order_id", "date", "product", "category", "quantity", "revenue", "cost", "profit", "discount", "status"]
    return out[cols].reset_index(drop=True), report


#: Dozwolone kodowania wejścia, w kolejności prób. Bez latin-1, które dekoduje
#: dowolną sekwencję bajtów i przez to nigdy nie sygnalizuje uszkodzonego pliku.
ALLOWED_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "cp1250")

#: Separatory spotykane w eksportach klientow. Ten sam zestaw co w upload_guard.
_SEPARATORS: tuple[str, ...] = (",", ";", "\t")

#: Ile bajtow probki wystarczy do rozpoznania separatora.
_SNIFF_BYTES = 65_536


def _sniff_separator(path: str, encoding: str) -> str:
    """Rozpoznaje separator z poczatku pliku, bez czytania calosci.

    Podnosi UnicodeDecodeError, jesli probka nie dekoduje sie danym kodowaniem —
    dzieki temu petla po kodowaniach w `read_client_csv` dziala jak dotychczas,
    tylko odrzuca zle kodowanie po 64 KiB zamiast po calym pliku.
    """
    with open(path, "rb") as fh:
        sample_bytes = fh.read(_SNIFF_BYTES)
    sample = sample_bytes.decode(encoding)
    # Ostatnia linia probki moze byc ucieta — nie karmimy nia sniffera.
    if "\n" in sample and len(sample_bytes) == _SNIFF_BYTES:
        sample = sample[: sample.rfind("\n")]
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(_SEPARATORS)).delimiter
    except csv.Error:
        # Sniffer odpuszcza np. przy jednej kolumnie — wybieramy najczestszy
        # kandydat z linii naglowka, a w ostatecznosci przecinek.
        head = sample.split("\n", 1)[0]
        counts = {d: head.count(d) for d in _SEPARATORS}
        best = max(counts, key=lambda k: counts[k])
        return best if counts[best] else ","


def read_client_csv(path: str) -> pd.DataFrame:
    """Odczyt CSV z auto-detekcją separatora (; , tab) i ścisłym dekodowaniem.

    Próbuje wyłącznie UTF-8-SIG, UTF-8 i CP1250. Gdy żadne nie zadziała,
    podnosi UnicodeDecodeError zamiast po cichu podmieniać bajty na U+FFFD:
    plik uszkodzony ma zostać odrzucony, a nie zamieniony w dane wyglądające
    poprawnie.
    """
    last_error: UnicodeDecodeError | None = None
    for enc in ALLOWED_ENCODINGS:
        try:
            sep = _sniff_separator(path, enc)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        try:
            # Silnik C zamiast `sep=None` + `engine="python"`: separator jest juz
            # znany z probki, wiec nie ma powodu parsowac calego pliku w Pythonie.
            return pd.read_csv(path, sep=sep, engine="c", encoding=enc, dtype=str)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        except pd.errors.ParserError:
            # Nietypowy uklad, ktory silnik C odrzuca — wracamy do starej sciezki,
            # zeby plik dzialajacy wczesniej nadal dzialal.
            return pd.read_csv(path, sep=None, engine="python", encoding=enc, dtype=str)
    if last_error is not None:
        raise last_error
    raise UnicodeDecodeError("utf-8", b"", 0, 1, "nieobsługiwane kodowanie pliku")
