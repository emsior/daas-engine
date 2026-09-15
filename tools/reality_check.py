# -*- coding: utf-8 -*-
"""
TWARDY TEST RZECZYWISTOSCI - warstwa 0 systemu kontroli.
Zero AI, zero narracji. Same fakty, ktore albo sa, albo ich nie ma.
Wynik: runtime/reality.json (biezacy) + runtime/reality_history.jsonl (historia).
Uruchomienie: python tools/reality_check.py
"""
import json, os, subprocess, sys, urllib.request, datetime, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "runtime"
OUT.mkdir(exist_ok=True)

def http_status(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "reality-check/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, len(r.read(4096))
    except urllib.error.HTTPError as e:
        return e.code, 0
    except Exception as e:
        return -1, 0

def git(*args):
    try:
        return subprocess.run(["git"] + list(args), cwd=str(ROOT),
                              capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""

checks = {}

# 1. Czy link-dowod zyje dla obcej osoby - LICZY SIE TRESC, NIE KOD HTTP.
#    Lekcja z 09.09: strona zwracala 200, a repo bylo puste. Status 200 nie jest dowodem.
def api(path):
    req = urllib.request.Request("https://api.github.com/repos/emsior/daas-engine" + path,
                                 headers={"User-Agent": "reality-check/1.0",
                                          "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return -1, ""

s_meta, b_meta = api("")
meta = json.loads(b_meta) if s_meta == 200 else {}
s_readme, _ = api("/contents/README.md")

checks["repo_istnieje"] = {
    "wartosc": s_meta == 200,
    "dowod": "API HTTP %s" % s_meta,
    "znaczenie": "repo w ogole istnieje pod tym adresem",
}
checks["repo_publiczne"] = {
    "wartosc": meta.get("private") is False,
    "dowod": "private=%s" % meta.get("private"),
    "znaczenie": "prywatne = obcy widzi 404",
}
# Lekcja z 10.09: pole `size` w API GitHuba aktualizuje sie z opoznieniem po pierwszym pushu
# i przez kilka godzin pokazuje 0 mimo dzialajacego repo. Falszywy negatyw.
# Wiarygodne sygnaly to README + liczba commitow widocznych dla obcego.
s_commits, b_commits = api("/commits?per_page=1")
n_commits = len(json.loads(b_commits)) if s_commits == 200 else 0
checks["repo_ma_kod"] = {
    "wartosc": s_readme == 200 and n_commits > 0,
    "dowod": "README HTTP %s, commits HTTP %s, size=%s KB (size bywa opoznione)" % (s_readme, s_commits, meta.get("size")),
    "znaczenie": "TO JEST WLASCIWY TEST. Puste publiczne repo wyglada gorzej niz 404 - jak porzucony projekt",
}

s_ci, b_ci = api("/actions/runs?per_page=1")
ci = (json.loads(b_ci).get("workflow_runs") or [{}])[0] if s_ci == 200 else {}
checks["ci_zielone"] = {
    "wartosc": ci.get("conclusion") == "success",
    "dowod": "%s / %s" % (ci.get("status"), ci.get("conclusion")),
    "znaczenie": "czerwony badge na publicznym repo jest gorszym dowodem niz brak badge",
}

s_sec, _ = api("/contents/.secrets")
checks["sekrety_nie_wyciekly"] = {
    "wartosc": s_sec == 404,
    "dowod": "/.secrets -> HTTP %s (404 = dobrze)" % s_sec,
    "znaczenie": "kontrola, czy katalog z tokenem nie trafil do publicznego repo",
}

# 2. Czy push w ogole poszedl
sb = git("status", "-sb").splitlines()
branch_line = sb[0] if sb else ""
checks["push_wykonany"] = {
    "wartosc": "origin/" in branch_line,
    "dowod": branch_line or "brak repo",
    "znaczenie": "brak 'origin/' = commity istnieja tylko na tym dysku",
}
checks["commity_lokalne"] = {
    "wartosc": len([l for l in git("log", "--oneline").splitlines() if l]),
    "dowod": git("log", "-1", "--format=%h %s"),
    "znaczenie": "liczba commitow - rosnie nawet gdy nic nie jest opublikowane",
}

# 3. Kanaly sprzedazy - czy cokolwiek jest publicznie widoczne
for nazwa, url in [
    ("profil_useme", "https://useme.com/pl/roles/contractor/"),
    ("landing_daas", "https://raporttygodniowy.pl"),
]:
    code, _ = http_status(url)
    checks[nazwa] = {"wartosc": code == 200, "dowod": "HTTP %s" % code,
                     "znaczenie": "kanal sprzedazy widoczny publicznie"}

# 4. Dowody CS2 - folder, ktory mial sie zapelnic
dow = pathlib.Path(os.path.expanduser("~")) / "Desktop" / "CS2Ops" / "dowody"
alt = pathlib.Path(os.environ.get("CS2_DOWODY_PATH", ""))
target = dow if dow.exists() else (alt if alt and alt.exists() else dow)
checks["zrzuty_cs2"] = {
    "wartosc": len(list(target.glob("*"))) if target.exists() else -1,
    "dowod": str(target),
    "znaczenie": "materialy dowodowe CS2",
}

# 5. Wideo demo - czy istnieje material do galerii
demo = ROOT / "runtime" / "reports"
checks["materialy_demo"] = {
    "wartosc": len(list(demo.rglob("*.png"))) if demo.exists() else 0,
    "dowod": str(demo),
    "znaczenie": "obrazy do galerii Fiverr",
}
checks["wideo_demo"] = {
    "wartosc": len(list(demo.rglob("*.mp4"))) if demo.exists() else 0,
    "dowod": "szukam *.mp4 w runtime/reports",
    "znaczenie": "wideo do galerii - zastepuje Loom",
}

# 6. Klucze dostepu - czy agent moze cokolwiek sam
checks["token_github"] = {
    "wartosc": (ROOT / ".secrets" / "gh_token.txt").exists(),
    "dowod": str(ROOT / ".secrets" / "gh_token.txt"),
    "znaczenie": "bez tego agent nie zrobi push ani nie przelaczy repo na public",
}

wynik = {
    "czas": datetime.datetime.now().isoformat(timespec="seconds"),
    "checks": checks,
    "werdykt_blokujacy": not checks["repo_ma_kod"]["wartosc"],
}

(OUT / "reality.json").write_text(json.dumps(wynik, ensure_ascii=False, indent=2), encoding="utf-8")
with (OUT / "reality_history.jsonl").open("a", encoding="utf-8") as f:
    f.write(json.dumps(wynik, ensure_ascii=False) + "\n")

print("TWARDY TEST RZECZYWISTOSCI -", wynik["czas"])
print("-" * 58)
for k, v in checks.items():
    w = v["wartosc"]
    flaga = "OK " if (w is True or (isinstance(w, int) and w > 0)) else "NIE"
    print("%-18s %-4s %s" % (k, flaga, v["dowod"]))
print("-" * 58)
print("BLOKADA SPRZEDAZY:", "TAK - nie wysylaj zadnej oferty" if wynik["werdykt_blokujacy"] else "nie")
