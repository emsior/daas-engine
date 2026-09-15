"""Heurystyczny mapper kolumn CSV klienta -> schemat EcommerceOrder.

Klienci przysyłają eksporty z Allegro, Shoper, WooCommerce, Baselinker, PrestaShop,
Excela "po swojemu". Zamiast wymagać idealnego CSV, mapujemy nagłówki po aliasach
(PL/EN), normalizujemy liczby w formacie polskim ("1 299,00 zł") i dopełniamy
brakujące kolumny (profit = revenue - cost, status = completed, category = "Inne").

Mapper NIGDY nie rzuca na brakujących kolumnach opcjonalnych — tylko na braku
minimum: identyfikator zamówienia (lub możliwość jego wygenerowania), data, przychód.
"""
from __future__ import annotations

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


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _to_number(v: Any) -> float | None:
    """'1 299,00 zł' -> 1299.0 ; '12.5%' -> 12.5 ; '' -> None."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.lower() in {"nan", "none", "null", "-"}:
        return None
    s = re.sub(r"[^\d,.\-]", "", s)
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
            out[col] = out[col].map(_to_number)

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
    out["status"] = out["status"].fillna("completed").astype(str).map(lambda s: STATUS_MAP.get(_norm(s), "completed"))

    # --- daty ---
    out["date"] = pd.to_datetime(out["date"], errors="coerce", dayfirst=True).dt.date
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
            return pd.read_csv(path, sep=None, engine="python", encoding=enc, dtype=str)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise UnicodeDecodeError("utf-8", b"", 0, 1, "nieobsługiwane kodowanie pliku")
