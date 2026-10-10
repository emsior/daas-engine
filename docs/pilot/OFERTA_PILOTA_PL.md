# Oferta Kontrolowanego Pilota Usługi DaaS

### Cel Pilota
Zweryfikowanie wartości biznesowej automatycznych raportów marżowych i wykrywania anomalii na realnych, historycznych lub bieżących danych Twojego biznesu w ciągu 14 dni.

### Warunki Pilota
- **Czas trwania:** 14 dni roboczych.
- **Zakres:** Przetworzenie do 4 eksportów tygodniowych lub jednego dużego eksportu historycznego (do 25 MB na plik).
- **Format wejściowy:** CSV / XLSX (eksport zamówień); opcjonalnie paczka JSON z lokalnego kolektora artefaktów.
- **Format wyjściowy:**
  - Raport analityczny HTML (do wydruku do PDF) oraz Markdown z metrykami: Przychód, Koszt, Zysk, Marża %, Średnia wartość zamówienia (AOV), Lista zamówień ze stratą, Dynamika tydzień-do-tygodnia (WoW).
  - Plik danych podsumowujących JSON.
- **Sposób pracy (do wyboru):**
  - **Kokpit:** logujesz się linkiem z e-maila (bez hasła), wgrywasz plik i pobierasz raport. Przed założeniem organizacji akceptujesz umowę powierzenia przetwarzania danych (`docs/DPA_v1_PL.md`).
  - **Operator:** przesyłasz plik, a raport dostarcza PPC. Nie wymaga to konta po Twojej stronie.
  - **API:** klucz API organizacji, przykłady w `docs/API.md` (np. wywołanie z n8n).
- **Koszt:** Ustalany indywidualnie (model pilota płatnego lub barterowego w zamian za referencję/feedback).

### Czego NIE obejmuje pilot (Explicit Out of Scope):
- Nie wymaga instalacji oprogramowania po stronie klienta.
- Nie obejmuje integracji z zewnętrznymi systemami (np. BaseLinker, Stripe) ani płatności online.
- Brak logowania przez SSO i kluczy dostępu (passkeys); logowanie odbywa się linkiem z e-maila.
- Brak umowy SLA na dostępność.
