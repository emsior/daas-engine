"""Generator fixture'ow UKSC (data/fixtures/uksc/*.json) + schematu JSON (schemas/uksc_collector_v1.json).

Uruchomienie: python tools/uksc_fixtures.py
Katalog kontroli v1 jest TU zrodlem prawdy dla fixture'ow; collector (tools/uksc-collector.ps1) musi
emitowac te same control_id i uksc_ref - test_uksc_pipeline.py pilnuje spojnosci z collectorem.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fixtures" / "uksc"
SCHEMA_OUT = ROOT / "schemas" / "uksc_collector_v1.json"

COLLECTED = "2026-09-22T08:15:00Z"
PZU = "OWU PZU Cyber UZ/162/2023"

# (control_id, uksc_ref, title, admin_required, reference_threshold, evidence_pass, evidence_fail)
CATALOG: list[tuple[str, str, str, bool, str | None, dict, dict | None]] = [
    ("ENC-01", "art. 8 ust. 1 pkt 2 lit. k", "Szyfrowanie woluminu systemowego (BitLocker)", True, None,
     {"volume": "C:", "protection": "On", "method": "XtsAes256", "pct": 100}, {"volume": "C:", "protection": "Off", "method": None, "pct": 0}),
    ("INT-01", "art. 8 ust. 1 pkt 5 lit. c", "Secure Boot wlaczony", True, None, {"secure_boot": True}, {"secure_boot": False}),
    ("INT-02", "art. 8 ust. 1 pkt 5 lit. c", "TPM obecny i gotowy", True, None, {"present": True, "ready": True, "version": "2.0"}, {"present": False, "ready": False, "version": None}),
    ("INT-03", "art. 8 ust. 1 pkt 5 lit. c", "Defender Tamper Protection", True, None, {"tamper_protected": True}, {"tamper_protected": False}),
    ("INT-04", "art. 8 ust. 1 pkt 5 lit. c", "BCD: testsigning / nointegritychecks wylaczone", True, None,
     {"testsigning": False, "nointegritychecks": False}, {"testsigning": True, "nointegritychecks": False}),
    ("INT-05", "art. 8 ust. 1 pkt 5 lit. c", "Sterowniki bez podpisu", True, None, {"unsigned_count": 0}, {"unsigned_count": 3, "sample": ["oem7.inf"]}),
    ("ACC-01", "art. 8 ust. 1 pkt 2 lit. n", "Konta w grupie Administratorzy", True, "referencja: <= 2 konta imienne + wbudowane",
     {"count": 2, "members": ["BUILTIN\\Administrator (disabled)", "DOMAIN\\it-admin"]}, {"count": 6, "members": ["Administrator", "user1", "user2", "user3", "kasa", "biuro"]}),
    ("ACC-02", "art. 8 ust. 1 pkt 2 lit. n", "Konto Gosc wylaczone", False, None, {"guest_enabled": False}, {"guest_enabled": True}),
    ("ACC-03", "art. 8 ust. 1 pkt 2 lit. n", "Konta wlaczone bez wymaganego hasla", False, None, {"count": 0}, {"count": 1, "accounts": ["kasa"]}),
    ("ACC-04", "art. 8 ust. 1 pkt 2 lit. n", "Polityka hasel (dlugosc, wiek, blokada)", True, "referencja: min. 12 znakow, blokada po <= 10 probach",
     {"min_length": 14, "max_age_days": 365, "lockout_threshold": 5, "history": 24}, {"min_length": 0, "max_age_days": 0, "lockout_threshold": 0, "history": 0}),
    ("ACC-05", "art. 8 ust. 1 pkt 2 lit. n", "UAC wlaczony z monitem", False, None,
     {"enable_lua": 1, "consent_prompt_admin": 5, "secure_desktop": 1}, {"enable_lua": 1, "consent_prompt_admin": 0, "secure_desktop": 0}),
    ("MON-01", "art. 8 ust. 1 pkt 2 lit. g", "Dziennik Security wlaczony, rozmiar i retencja", True, "referencja: >= 64 MB, bez nadpisywania < 30 dni",
     {"enabled": True, "max_size_mb": 196, "oldest_event_days": 41}, {"enabled": True, "max_size_mb": 20, "oldest_event_days": 2}),
    ("MON-02", "art. 8 ust. 1 pkt 2 lit. g", "Polityka audytu: logowanie i zarzadzanie kontami", True, None,
     {"logon": "Success and Failure", "account_management": "Success and Failure"}, {"logon": "No Auditing", "account_management": "No Auditing"}),
    ("UPD-01", "art. 8 ust. 1 pkt 5 lit. b", "Dni od ostatniej poprawki systemu", False, f"{PZU}: krytyczne poprawki <= 30 dni",
     {"last_hotfix": "2026-09-10", "days_since": 12, "last_hotfix_id": "KB5065426"}, {"last_hotfix": "2026-05-14", "days_since": 131, "last_hotfix_id": "KB5058411"}),
    ("UPD-02", "art. 8 ust. 1 pkt 5 lit. b", "Brak oczekujacego restartu po aktualizacji", False, None, {"reboot_pending": False}, {"reboot_pending": True}),
    ("UPD-03", "zal. 4 pkt 16", "Wersja systemu wspierana przez producenta", False, None,
     {"os": "Windows 11 Pro", "build": "26200", "supported": True}, {"os": "Windows 10 Pro", "build": "19045", "supported": False, "eol": "2025-10-14"}),
    ("AV-01", "zal. 4 pkt 13", "Ochrona antywirusowa aktywna (Defender lub inny)", False, f"{PZU}: aktywny antywirus na wszystkich punktach koncowych",
     {"product": "Microsoft Defender", "realtime": True, "third_party": []}, {"product": "Microsoft Defender", "realtime": False, "third_party": []}),
    ("AV-02", "zal. 4 pkt 13", "Wiek sygnatur antywirusa", False, "referencja: <= 7 dni",
     {"signature_age_days": 1, "last_full_scan_days": 6}, {"signature_age_days": 23, "last_full_scan_days": 78}),
    ("BKP-01", "art. 8 ust. 1 pkt 2 lit. f; zal. 4 pkt 10", "Agent kopii zapasowej obecny na stacji", False, f"{PZU}: kopia do izolowanego srodowiska co <= 7 dni",
     {"agents": ["VeeamEndpointBackupSvc"], "last_backup_hint": None}, {"agents": [], "last_backup_hint": None}),
    ("BKP-02", "zal. 4 pkt 11", "Test odtworzenia kopii zapasowej", False, f"{PZU}: test odtworzenia co <= 365 dni",
     {"attestation_hint": "protokol z testu odtworzenia (data, zakres, wynik) - pole last_test_date"}, None),
    ("MFA-01", "art. 8 ust. 1 pkt 2 lit. l", "MFA dla dostepu zdalnego i kont uprzywilejowanych", False, f"{PZU}: MFA dla zdalnego dostepu",
     {"attestation_hint": "raport rejestracji MFA z Entra ID / VPN (v1.1: automatycznie przez Graph API)", "hello_for_business_policy": True}, None),
    ("FW-01", "art. 8 ust. 1 pkt 5 lit. a", "Zapora Windows wlaczona na wszystkich profilach", False, f"{PZU}: aktywna zapora sieciowa",
     {"profiles": {"Domain": True, "Private": True, "Public": True}, "enabled_all_profiles": True, "rdp_rule_present": False, "rdp_rule_profiles": []},
     {"profiles": {"Domain": True, "Private": False, "Public": True}, "enabled_all_profiles": False, "rdp_rule_present": True, "rdp_rule_profiles": ["Private", "Public"]}),
    ("RDP-01", "art. 8 ust. 1 pkt 5 lit. a", "RDP wylaczony albo chroniony (NLA + zapora)", False, f"{PZU}: RDP wylaczony, chyba ze chroniony MFA",
     {"rdp_enabled": False, "listening_3389": False, "nla_required": True}, {"rdp_enabled": True, "listening_3389": True, "nla_required": False}),
    ("HYG-01", "art. 8 ust. 1 pkt 2 lit. j", "Blokada sesji po bezczynnosci", False, "referencja: <= 15 min",
     {"inactivity_timeout_sec": 600, "screensaver_secure": True}, {"inactivity_timeout_sec": 0, "screensaver_secure": False}),
    ("HYG-02", "art. 8 ust. 1 pkt 2 lit. j", "SMBv1 wylaczony", True, None, {"smb1_enabled": False}, {"smb1_enabled": True}),
    ("HYG-03", "art. 8 ust. 1 pkt 2 lit. j", "Wykluczenia Defendera obejmujace cale drzewa", True, None,
     {"exclusions_total": 1, "tree_exclusions": []}, {"exclusions_total": 4, "tree_exclusions": ["C:\\", "C:\\Users"]}),
    ("HYG-04", "art. 8 ust. 1 pkt 2 lit. j", "Wpisy autostartu / zadania / uslugi bez podpisu", True, None,
     {"unsigned_autoruns": 0, "unsigned_tasks": 0, "unsigned_services": 0}, {"unsigned_autoruns": 3, "unsigned_tasks": 1, "unsigned_services": 0}),
    ("AST-01", "art. 8 ust. 1 pkt 2 lit. m; zal. 4 pkt 1", "Inwentarz stacji i oprogramowania zebrany", False, None,
     {"software_count": 84, "hardware": True}, None),
    ("GOV-01", "art. 8 ust. 1 pkt 2 lit. a", "Polityka bezpieczenstwa / dokumentacja SZBI", False, None,
     {"attestation_hint": "dokument polityki z data zatwierdzenia i wlascicielem"}, None),
    ("GOV-02", "art. 8 ust. 1 pkt 4", "Procedura zarzadzania incydentami (24h/72h/1 mies.)", False, None,
     {"attestation_hint": "procedura z kontaktem do CSIRT sektorowego"}, None),
    ("GOV-03", "art. 8 ust. 1 pkt 2 lit. i", "Szkolenia z cyberbezpieczenstwa (w tym kierownik, raz w roku)", False, None,
     {"attestation_hint": "lista szkolen z datami i podpisami"}, None),
    ("GOV-04", "art. 8 ust. 1 pkt 2 lit. e", "Rejestr dostawcow ICT i klauzule bezpieczenstwa", False, None,
     {"attestation_hint": "wykaz dostawcow + umowa z MSP (art. 14)"}, None),
]

MANUAL_IDS = {"BKP-02", "MFA-01", "GOV-01", "GOV-02", "GOV-03", "GOV-04"}


def _ev_hash(evidence: dict) -> str:
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()


def _pkg_hash(hashes: list[str]) -> str:
    return hashlib.sha256("".join(sorted(hashes)).encode("ascii")).hexdigest()


def build(kind: str) -> dict:
    is_admin = kind != "no_admin"
    checks = []
    for cid, ref, title, admin_req, thr, ev_pass, ev_fail in CATALOG:
        if cid in MANUAL_IDS:
            status, ev = "MANUAL", ev_pass
        elif not is_admin and admin_req:
            status, ev = "NA_NO_ADMIN", {"reason": "collector run without administrator rights"}
        elif kind == "noncompliant" and ev_fail is not None:
            status, ev = ("WARN" if cid in {"ACC-01", "AV-02", "HYG-04", "UPD-02"} else "FAIL"), ev_fail
        else:
            status, ev = "PASS", ev_pass
        checks.append({
            "control_id": cid, "uksc_ref": ref, "title": title, "status": status, "evidence": ev,
            "evidence_source": "powershell", "evidence_collected_at": COLLECTED, "evidence_hash": _ev_hash(ev),
            "reference_threshold": thr,
        })
    host = {"name": {"compliant": "WS-KSIEGOWOSC-01", "noncompliant": "WS-PRODUKCJA-07", "no_admin": "WS-BIURO-03"}[kind],
            "os": "Windows 11 Pro" if kind != "noncompliant" else "Windows 10 Pro",
            "build": "26200" if kind != "noncompliant" else "19045",
            "domain_joined": kind != "no_admin", "is_admin_run": is_admin}
    inventory = {"software": [
        {"name": "Microsoft 365 Apps for business", "version": "16.0.19127.20000", "publisher": "Microsoft Corporation"},
        {"name": "7-Zip 24.09 (x64)", "version": "24.09", "publisher": "Igor Pavlov"},
        {"name": "Google Chrome", "version": "140.0.7339.128", "publisher": "Google LLC"},
    ], "hardware": {"cpu": "Intel Core i5-12400", "ram_gb": 16, "disk_gb": 512, "serial": "REDACTED-IN-FIXTURE"}}
    return {
        "schema_version": "1.0", "collector_version": "0.1.0", "collected_at_utc": COLLECTED,
        "host": host, "checks": checks, "inventory": inventory,
        "package_sha256": _pkg_hash([c["evidence_hash"] for c in checks]),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for kind, name in (("compliant", "host_compliant.json"), ("noncompliant", "host_noncompliant.json"), ("no_admin", "host_no_admin.json")):
        (OUT / name).write_text(json.dumps(build(kind), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote", OUT / name)
    from app.core.models import (
        UkscPackage,  # noqa: WPS433 - import lokalny, zeby skrypt dzialal bez instalacji pakietu
    )
    SCHEMA_OUT.parent.mkdir(parents=True, exist_ok=True)
    schema = UkscPackage.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://prochpc.pl/schemas/uksc_collector_v1.json"
    SCHEMA_OUT.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", SCHEMA_OUT)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    main()
