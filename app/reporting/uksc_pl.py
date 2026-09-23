"""Polskie nazwy do raportu UKSC (warstwa prezentacji).

Collector PowerShell i fixtures trzymają tytuły w ASCII: Windows PowerShell 5.1 czyta skrypty
UTF-8 bez BOM jako ANSI, więc polskie znaki w .ps1 byłyby zepsute. Dane (paczka, hashe, DuckDB)
zostają bez zmian. Tu mapujemy wyłącznie to, co widzi czytelnik raportu.
Brak wpisu = tytuł z paczki (nowa kontrola w collectorze nie psuje raportu).
"""
from __future__ import annotations

TITLES: dict[str, str] = {
    "ENC-01": "Szyfrowanie woluminu systemowego (BitLocker)",
    "INT-01": "Secure Boot włączony",
    "INT-02": "TPM obecny i gotowy",
    "INT-03": "Defender Tamper Protection",
    "INT-04": "BCD: testsigning / nointegritychecks wyłączone",
    "INT-05": "Sterowniki bez podpisu",
    "ACC-01": "Konta w grupie Administratorzy",
    "ACC-02": "Konto Gość wyłączone",
    "ACC-03": "Konta włączone bez wymaganego hasła",
    "ACC-04": "Polityka haseł (długość, wiek, blokada)",
    "ACC-05": "UAC włączony z monitem",
    "MON-01": "Dziennik Security włączony, rozmiar i retencja",
    "MON-02": "Polityka audytu: logowanie i zarządzanie kontami",
    "UPD-01": "Dni od ostatniej poprawki systemu",
    "UPD-02": "Brak oczekującego restartu po aktualizacji",
    "UPD-03": "Wersja systemu wspierana przez producenta",
    "AV-01": "Ochrona antywirusowa aktywna (Defender lub inny)",
    "AV-02": "Wiek sygnatur antywirusa",
    "BKP-01": "Agent kopii zapasowej obecny na stacji",
    "BKP-02": "Test odtworzenia kopii zapasowej",
    "MFA-01": "MFA dla dostępu zdalnego i kont uprzywilejowanych",
    "FW-01": "Zapora Windows włączona na wszystkich profilach",
    "RDP-01": "RDP wyłączony albo chroniony (NLA + zapora)",
    "HYG-01": "Blokada sesji po bezczynności",
    "HYG-02": "SMBv1 wyłączony",
    "HYG-03": "Wykluczenia Defendera obejmujące całe drzewa",
    "HYG-04": "Wpisy autostartu / zadania / usługi bez podpisu",
    "AST-01": "Inwentarz stacji i oprogramowania zebrany",
    "GOV-01": "Polityka bezpieczeństwa / dokumentacja SZBI",
    "GOV-02": "Procedura zarządzania incydentami (24 h / 72 h / 1 mies.)",
    "GOV-03": "Szkolenia z cyberbezpieczeństwa (w tym kierownik, raz w roku)",
    "GOV-04": "Rejestr dostawców ICT i klauzule bezpieczeństwa",
}

HINTS: dict[str, str] = {
    "BKP-02": "protokół z testu odtworzenia (data, zakres, wynik) — pole last_test_date",
    "MFA-01": "raport rejestracji MFA z Entra ID / VPN (v1.1: automatycznie przez Graph API)",
    "GOV-01": "dokument polityki z datą zatwierdzenia i właścicielem",
    "GOV-02": "procedura z kontaktem do CSIRT sektorowego",
    "GOV-03": "lista szkoleń z datami i podpisami",
    "GOV-04": "wykaz dostawców + umowa z MSP (art. 14)",
}

STATUS_LABEL: dict[str, str] = {
    "PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN", "MANUAL": "poświadczenie",
    "NA_NO_ADMIN": "brak uprawnień admina", "ERROR": "błąd zbierania",
}


def title(control_id: str | None, fallback: str | None) -> str:
    return TITLES.get(control_id or "", fallback or "-")


def hint(control_id: str | None, fallback: str | None) -> str | None:
    return HINTS.get(control_id or "", fallback)


def ref(uksc_ref: str | None) -> str:
    """Odniesienie do ustawy w zapisie dla czytelnika: 'zal.' -> 'zał.'."""
    return (uksc_ref or "brak odniesienia").replace("zal. ", "zał. ")
