# Plan Archiwizacji i Konsolidacji Kopii Roboczych (2026-09-14)

Zasada naczelna: **Zero usuwania danych w trakcie sprintu.**  
Wszystkie operacje poniżej są propozycjami do wykonania w kontrolowany sposób po akceptacji operatora.

---

### 1. D:\daas-engine
- **CURRENT LOCATION:** `D:\daas-engine`
- **PROPOSED STATUS:** **JEDYNA AKTYWNA KOPIA (Source of Truth)**
- **REASON:** Zawiera pełne środowisko robocze (.venv), działające testy (52 passed), poprawki bezpieczeństwa (P0 path traversal fix, OPSEC sanitize) oraz gałąź integracyjną `sprint/2026-09-14-product-mvp`.
- **SAFE ACTION:** Utrzymanie jako głównego repozytorium developerskiego i operacyjnego.
- **ROLLBACK:** Kopia zapasowa w `D:\_sprint_backups\daas_2026-09-14\daas-engine-D.bundle`.

---

### 2. C:\Users\proch\Desktop\workspace\daas-engine
- **CURRENT LOCATION:** `C:\Users\proch\Desktop\workspace\daas-engine`
- **PROPOSED STATUS:** **FROZEN / READ-ONLY ARCHIVE**
- **REASON:** Kopia historyczna, z której wartościowe commity (README EN/PL, LICENSE, tools) zostały włączone do gałęzi integracyjnej w D:. Posiadała martwy `index.lock` i niesanitizowane dane w `pyproject.toml`.
- **SAFE ACTION:** Oznaczenie katalogu jako archiwalny (np. dodanie pliku `ZAMROZONE_ARCHIWUM.txt`), zaprzestanie edycji w tym folderze.
- **ROLLBACK:** Kopia zapasowa w `D:\_sprint_backups\daas_2026-09-14\daas-engine-workspace.bundle`.

---

### 3. Backup_C_2026-09-11
- **CURRENT LOCATION:** `C:\...` (lokalne foldery backupu sprzed sprintu)
- **PROPOSED STATUS:** **ARCHIWUM ZIMNE (Cold Storage)**
- **REASON:** Statyczny stan z 11.09.2026, służący wyłącznie do audytu historycznego.
- **SAFE ACTION:** Pozostawienie bez zmian, brak modyfikacji, brak dodawania do indeksów git.
- **ROLLBACK:** Nienaruszony stan pierwotny.

---

### 4. c1
- **CURRENT LOCATION:** Foldery robocze `c1`
- **PROPOSED STATUS:** **ARCHIWUM TYMCZASOWE**
- **REASON:** Pozostałości po wczesnych testach modułów.
- **SAFE ACTION:** Pozostawienie w stanie obecnym do weryfikacji manualnej.
- **ROLLBACK:** Nienaruszony.

---

### 5. D:\daas-engine\.agents-proponowane
- **CURRENT LOCATION:** `D:\daas-engine\.agents-proponowane/`
- **PROPOSED STATUS:** **DO OSOBNEGO PRZEGLĄDU (Staging / Design Docs)**
- **REASON:** Zawiera koncepcyjne opisy ról Claude/Antigravity. Nie jest częścią kodu produkcyjnego aplikacji DaaS Engine.
- **SAFE ACTION:** Pozostawienie w stanie untracked (w `.gitignore` lub osobnym folderze dokumentacji projektowej).
- **ROLLBACK:** Brak wpływu na kod aplikacji i testy.
