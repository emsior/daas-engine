# System projektowy kokpitu „DaaS Engine Cloud” (PPC)

Wersja 1.0 · 09.10.2026 · autor: Designer (faza BUILD) · wejście: `RESEARCHER_UX.md`, `SPEC_v2.md` §3.

Kierunek: rzeczowy, urzędowo-przyjazny. Granat jako kolor zaufania, dużo bieli, jedna akcja główna na ekranie, status zawsze jako **ikona + tekst + kolor**. Bez ozdobników: żadnych gradientów, szkła, 3D ani animacji dekoracyjnych.

## 1. Pliki

| Plik                            | Rola                                                                                                                                                                                                         |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `web/src/styles/tokens.css`     | trzy poziomy tokenów (primitive, semantic, component), tryby light/dark, typografia, spacing, ruch                                                                                                           |
| `web/src/styles/base.css`       | reset minimalny, `body`, nagłówki, linki, fokus, `.skip-link`, `.sr-only`, układ (`.layout-container`, `.stack-*`, `.cluster`, `.grid-auto`), `.tabular`, `.hash`, `prefers-reduced-motion`, `forced-colors` |
| `web/src/styles/components.css` | komponenty bez JS: `.btn`, `.input`, `.field-error`, `.error-summary`, `.card`, `.kpi`, `.badge`, tabela, `.dropzone`, `.progress`, `.alert`, `.skeleton`, `.iframe-report`, `.toast`, `.code`               |
| `web/src/styles/tailwind.css`   | punkt wejścia: Tailwind v4 + tokeny + mapowanie `@theme` + warstwy                                                                                                                                           |

W `app/layout.tsx` importuj **tylko** `../styles/tailwind.css`.

## 2. Zasady użycia tokenów

1. **Trzy poziomy.** _Primitive_ (`--slate-900`, `--sky-300`) to surowe wartości — nie używaj ich poza `tokens.css`. _Semantic_ (`--color-text`, `--color-primary`) to role i jedyny poziom, który zmienia się między trybami. _Component_ (`--button-primary-bg`, `--badge-ok-fg`) wyprowadza decyzje komponentów z poziomu semantic. Zmiana wartości semantic propaguje się na cały kokpit.
2. **W komponentach i stronach używaj poziomu semantic lub component.** Zakazane: kolory szesnastkowe, `rgb()`, nazwy kolorów CSS (poza słowami kluczowymi systemowymi w `forced-colors`), klasy Tailwinda z surowych skal (skala `--color-*` jest wyzerowana — `bg-red-500` nie istnieje).
3. **Spacing wyłącznie z siatki 4 px:** `--space-1…--space-16` (4, 8, 12, 16, 24, 32, 40, 48, 64; `--space-5` = 20 jest na siatce, ale poza skalą raportu UX — używaj oszczędnie). W Tailwindzie `p-4` = 16 px, `gap-6` = 24 px. Zakaz 13 px, 15 px, 37 px, `0.618rem`.
4. **Typografia wyłącznie ze skali** `--text-xs…--text-4xl` (z interlinią `--text-*--line-height`). Nagłówki h1–h4 stylizuje `base.css`; rozmiary mobilne/desktopowe przełącza się na `--heading-*` przy 640 px.
5. **Kolor nigdy nie jest jedynym nośnikiem znaczenia** (1.4.1): status = ikona (kształt) + tekst + kolor; linki zawsze podkreślone; stan błędu pola = obrys + wewnętrzny pierścień + ikona + tekst.
6. **Tekst >= 4,5:1, duży tekst >= 3:1, elementy UI >= 3:1.** `--color-border-subtle` jest **celowo dekoracyjny** (ok. 1,4:1 na bieli): używaj go tylko dla krawędzi kart, separatorów i szkieletów, nigdy dla obrysu pola, strefy upuszczania, toru paska ani innego elementu identyfikującego kontrolkę (tam `--color-border`).
7. **Ruch:** tylko `opacity`, `transform`, kolor; `--duration-fast` 150 ms (przyciski, odznaki), `--duration-base` 220 ms (karta, panel, toast). Przy `prefers-reduced-motion: reduce` czasy spadają do 0 ms, a animacje (szkielet, toast) są wyłączone. Brak nieskończonych pętli poza szkieletem.
8. **Brak tekstów w tokenach** — treści (etykiety statusów, komunikaty błędów) należą do słowników PL/EN, nie do CSS. Jedyne znaki w CSS to ikony statusów (✓ ▲ ✕ … ? i), ukryte dla czytników.
9. **Brak zasobów zewnętrznych:** fonty systemowe, ikony jako znaki tekstowe, zero `url(...)`. CSP nie wymaga `img-src data:` dla stylów.

### Tryby light / dark

- Domyślnie `:root` = light.
- Dark włącza się: (a) preferencją systemu, o ile `<html>` nie ma `data-theme="light"`; (b) `<html data-theme="dark">`.
- `data-theme` ustawiaj **wyłącznie na `<html>`** (tokeny komponentów liczą się na `:root`).
- Blok dark występuje w `tokens.css` dwa razy (media query + atrybut) — każdą zmianę wprowadź w obu.
- Nie używaj wariantu `dark:` Tailwinda — tokeny semantic zmieniają się same.

## 3. Komponenty

| Komponent           | Klasy / atrybuty                                                                                                         | Stany                                                                                           | Uwagi                                                                                                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Przycisk            | `.btn` (= primary), `.btn-secondary`, `.btn-ghost`, `.btn-danger`, `.btn-cta`                                            | `:hover`, `:active`, `:focus-visible`, `:disabled` / `aria-disabled="true"`, `aria-busy="true"` | min 24 px (AA), standard 40 px, `.btn-cta` 44 px i pełna szerokość < 640 px. Loading: tekst + `…`, bez spinnera; komunikat „Trwa przetwarzanie…” w regionie `aria-live` |
| Pole                | `.field` > `.label` + `.hint` + `.input` + `.field-error`                                                                | hover, focus-visible, `aria-invalid="true"`, `disabled`                                         | 44 px < 640 px, czcionka 16 px (brak zoomu iOS). Błąd: `aria-describedby` wskazuje `.hint` i `.field-error`                                                             |
| Błąd pola           | `.field-error`                                                                                                           | —                                                                                               | ikona ✕ + tekst + kolor. Treść: co poszło źle i jak naprawić                                                                                                            |
| Podsumowanie błędów | `.error-summary` > `.error-summary__title` + `ul.error-summary__list` > `a`                                              | fokus programowy (`tabindex="-1"`)                                                              | pokazuj także przy jednym błędzie; każdy link prowadzi do pola                                                                                                          |
| Karta               | `.card`, `.card__title`, `.card-interactive`                                                                             | hover (obrys + cień), `:has(:focus-visible)`                                                    | wnętrze 16 px < 640 px, 24 px >= 640 px                                                                                                                                 |
| KPI                 | `dl.kpi` > `dt.kpi__label` + `dd.kpi__value` (+ `.kpi__hint`)                                                            | —                                                                                               | etykieta tekstowa NAD liczbą; cyfry tabelaryczne; max 3 na ekran raportu                                                                                                |
| Odznaka statusu     | `.badge-ok`, `.badge-warn`, `.badge-error`, `.badge-pending`, `.badge-unknown`                                           | —                                                                                               | ikona w `::before` (✓ ▲ ✕ … ?), nazwa statusu w tekście HTML („Zgodne”, „Ostrzeżenie”, „Błąd”, „W toku”, „Nieznany”)                                                    |
| Tabela              | `table.data-table` (alias `.table`) w `.table-wrap`, `td[data-label]`, `.num`                                            | hover wiersza, pasy                                                                             | < 640 px wiersze jako karty (etykieta z `data-label`). Z Tailwindem używaj `.data-table` (patrz §5)                                                                     |
| Strefa pliku        | `.dropzone[data-state="idle\|active\|error"]`, `.dropzone__title`, `.dropzone__hint`                                     | idle (obrys przerywany), active (ciągły, niebieski), error (ciągły, czerwony)                   | w środku natywny `<input type="file" class="sr-only">` + widoczny `.btn` „Wybierz plik” (alternatywa dla przeciągania, 2.5.7)                                           |
| Postęp              | `<progress class="progress" value max>`                                                                                  | determinate                                                                                     | szerokość z `value`, **nie** z `style=""`; zawsze podawaj `value`; tekst „wiersz X z Y” osobno                                                                          |
| Alert               | `.alert-info`, `.alert-success`, `.alert-warning`, `.alert-danger` (z `.alert`) > jeden element potomny z `.alert-title` | —                                                                                               | ikona kołowa (i ✓ ▲ ✕) + gruby lewy brzeg + tekst. Blokujące błędy tylko jako alert/podsumowanie                                                                        |
| Szkielet            | `.skeleton`, `.skeleton-text`, `.skeleton-kpi`                                                                           | puls tylko bez reduced-motion                                                                   | kontener `aria-busy="true"`, szkielet `aria-hidden="true"`                                                                                                              |
| Raport              | `iframe.iframe-report`                                                                                                   | —                                                                                               | `sandbox=""`, `title`, min 32 rem wysokości, jasne płótno niezależnie od motywu                                                                                         |
| Toast               | `.toast` (kontener `role="status"`)                                                                                      | wejście 220 ms (bez reduced-motion)                                                             | tylko potwierdzenia niekrytyczne („Skopiowano”); nigdy błędy blokujące; min. 5 s lub do zamknięcia                                                                      |
| Kod                 | `.code`, `.hash` + `.hash-group`                                                                                         | —                                                                                               | komenda kolektora, skrót SHA-256                                                                                                                                        |

### Układ i narzędzia

| Klasa                                                      | Działanie                                                                                                                                          |
| ---------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.layout-container` (alias `.container`)                   | szerokość maks. `--container-max` (1200 px), padding boczny `--gutter` (16 px, 24 px >= 640 px); `.container-narrow` zawęża do 768 px (formularze) |
| `.stack-1/2/3/4/6/8/12`                                    | pionowy stos z odstępem z tokenów                                                                                                                  |
| `.cluster`, `.cluster-2`, `.cluster-4`, `.cluster-between` | poziomy klaster z zawijaniem (przyciski, odznaki, metadane)                                                                                        |
| `.grid-auto`                                               | `auto-fit` kolumny >= 16 rem (nadpisz `--grid-min` klasą wariantu lub w CSS)                                                                       |
| `.tabular`, `.nowrap`, `.text-muted`                       | liczby tabelaryczne, brak zawijania, tekst pomocniczy                                                                                              |
| `.sr-only`, `.sr-only-focusable`, `.skip-link`             | tylko dla czytników; link „Przejdź do treści”                                                                                                      |

## 4. Siatka i breakpointy

| Punkt           | Wartość          | Zmiany                                                                                                        |
| --------------- | ---------------- | ------------------------------------------------------------------------------------------------------------- |
| bazowy (mobile) | 375 px           | jedna kolumna, gutter 16 px, h1 28/36, h2 24/32, `.btn-cta` pełna szerokość, tabele jako karty                |
| sm              | 640 px (`40em`)  | gutter 24 px, h1 36/44, h2 30/38, tabele jako tabele, karta z paddingiem 24 px, `.grid-auto` z odstępem 24 px |
| lg              | 1024 px (`64em`) | układy wielokolumnowe (np. kokpit: raport tygodnia + wgrywanie obok siebie)                                   |
| xl              | 1280 px (`80em`) | docelowy desktop; kontener ogranicza treść do 1200 px                                                         |

Tailwind: warianty `sm:`, `lg:`, `xl:` (wartości jak wyżej; `md` i `2xl` celowo usunięte). Mobile-first: reguły bazowe dotyczą 375 px, warianty dokładają.

Układ stron: zawartość zaczyna się od `h1` w pierwszym ekranie; **bez sekcji hero** — na ekranie głównym karta „Wgraj plik” i ostatni raport są widoczne bez przewijania na 375 × 667.

## 5. Integracja z Tailwind v4

### Kolejność w `tailwind.css`

```css
@import "tailwindcss";
@import "./tokens.css"; /* BEZ layer() — patrz niżej */
@import "./base.css" layer(base); /* po preflight, w tej samej warstwie base */
@import "./components.css" layer(components); /* narzędzia Tailwinda je nadpisują */
```

Wybór: `@import ... layer()` zamiast `@layer base { @import }`, bo jest zgodny ze standardem CSS i z dokumentacją Tailwinda. `components.css` w warstwie `components` sprawia, że `class="btn mt-4"` działa (narzędzie wygrywa).

**Niezmiennik:** `tokens.css` musi pozostać poza warstwą. Deklaracje bez warstwy są silniejsze od deklaracji z `@layer theme`, więc wartości z `tokens.css` (w tym tryb dark) zawsze wygrywają z tym, co Tailwind wygeneruje z `@theme`.

### Gotowy blok `@theme` (wycinek; pełny w `tailwind.css`)

```css
@theme {
  --color-*: initial; /* brak surowych skal Tailwinda */
  --font-*: initial;
  --text-*: initial;
  --radius-*: initial;
  --shadow-*: initial;
  --breakpoint-*: initial;
  --breakpoint-sm: 40rem;
  --breakpoint-lg: 64rem;
  --breakpoint-xl: 80rem;
}

@theme inline {
  --color-bg: var(--color-bg);
  --color-surface: var(--color-surface);
  --color-text: var(--color-text);
  --color-text-muted: var(--color-text-muted);
  --color-primary: var(--color-primary);
  --color-on-primary: var(--color-on-primary);
  --color-success: var(--color-success);
  --color-success-bg: var(--color-success-bg);
  --color-warning: var(--color-warning);
  --color-warning-bg: var(--color-warning-bg);
  --color-danger: var(--color-danger);
  --color-danger-bg: var(--color-danger-bg);
  --color-border: var(--color-border);
  --color-focus: var(--color-focus);
  --color-fg: var(--color-text); /* alias: text-fg */
  --color-fg-muted: var(--color-text-muted); /* alias: text-fg-muted */

  --font-sans: var(--font-sans);
  --font-mono: var(--font-mono);

  --text-base: var(--text-base);
  --text-base--line-height: var(--text-base--line-height);
  /* …xs, sm, lg, xl, 2xl, 3xl, 4xl analogicznie */

  --radius-md: var(--radius-md);
  --shadow-sm: var(--shadow-sm);
  --spacing: var(--space-1); /* siatka 4 px */
}
```

Przykłady klas: `bg-surface text-fg border-border`, `text-danger`, `bg-success-bg text-success`, `font-mono text-sm`, `rounded-md shadow-sm`, `p-4 gap-6`.

Nazwy po obu stronach są te same celowo — Tailwind rejestruje narzędzie, a wartość zawsze pochodzi z `tokens.css`. **Do potwierdzenia pierwszym buildem** (tego bloku nie uruchamiano). Jeśli build zgłosi cykliczne odwołanie lub narzędzia nie dostaną wartości, awaryjnie: usuń blok `@theme inline` i używaj składni `bg-(--color-primary)`, `text-(--color-danger)` — nie wymaga rejestracji i daje ten sam wynik.

### Kolizje nazw klas z Tailwindem

| Nasza klasa  | Narzędzie Tailwinda                                                           | Rozwiązanie                         |
| ------------ | ----------------------------------------------------------------------------- | ----------------------------------- |
| `.container` | `container` (max-width per breakpoint)                                        | używaj `.layout-container`          |
| `.table`     | `table` (`display: table`), wygrywa warstwą utilities i psuje karty na mobile | używaj `.data-table`                |
| `.sr-only`   | `sr-only`                                                                     | identyczne działanie, bez konfliktu |

Tailwind generuje narzędzie, gdy gdziekolwiek w skanowanych plikach pojawi się słowo `table` lub `container`, więc nie polegaj na tym, że „nie używam tej klasy”.

## 6. Wytyczne dla Frontend Dev

### Fokus

- Po nawigacji klienckiej (zmiana trasy) przenieś fokus na `<h1 tabindex="-1">` nowego widoku i zaktualizuj `document.title`. Nie przenoś na `body`.
- Po wysłaniu formularza z błędami: fokus na `.error-summary` (`tabindex="-1"`, programowo, bez animacji). Nie dodawaj jednocześnie `role="alert"` — czytnik ogłosi dwa razy.
- Po sukcesie uploadu: fokus na nagłówek widoku raportu albo na komunikat sukcesu (`.alert-success`), nie na przycisk, który zniknął.
- Sticky elementy (nagłówek, toast) nie mogą zasłaniać fokusu (2.4.11): `html` ma `scroll-padding-block: 4rem`; sticky nagłówek niższy niż 64 px.
- Nie usuwaj `outline` — reguła `:focus-visible` w `base.css` jest globalna (2 px, kolor `--color-focus`, offset 2 px).

### `aria-live`

- Jeden stały region `<div role="status" aria-live="polite" class="sr-only">` w layoucie, poza fragmentami podmienianymi; wpisuj do niego komunikaty („Plik wgrany”, „Skopiowano skrót”).
- Postęp uploadu: tekst „Sprawdzam plik: wiersz 340 z 1 200” aktualizuj co kilka procent lub co 1–2 s, nie przy każdym wierszu. `<progress>` ma `aria-label` lub `aria-labelledby` wskazujące ten tekst.
- Błędy blokujące: `.error-summary` z fokusem (nie toast). Toast tylko dla potwierdzeń.

### Podsumowanie błędów

- Struktura i treść: patrz tabela §3. Tytuł typu „Wystąpił problem”, lista linków `href="#id-pola"`; pole ma `aria-invalid="true"` i `aria-describedby`. Wpisy użytkownika zachowaj po błędzie. Błąd nie znika sam (2.2.1).

### Przełącznik light / dark

- Trzy stany: systemowy (brak atrybutu), light, dark. Zapisz wybór w ciasteczku `theme` i ustaw `data-theme` w `layout.tsx` po stronie serwera — bez skryptu inline (CSP) i bez błysku złego motywu.
- Dodaj `<meta name="color-scheme" content="light dark">`.
- Przełącznik to `button` z `aria-pressed` lub grupa radio z etykietami tekstowymi („Jasny”, „Ciemny”, „Systemowy”), nie sama ikona.

### CSP strict

- **Zakaz atrybutów `style=""` i właściwości `style` w JSX** (renderują się jako atrybut i są blokowane). Zmienne wartości: `<progress value>`, klasy wariantów, `data-*` + selektor atrybutu.
- Inline `<style>` wolno wyłącznie w budowanym CSS (nonce z middleware).
- Zero fontów i ikon z CDN; ikony statusów to znaki tekstowe w CSS (`content`), SVG tylko inline z JSX bez `style`.

### Ramka z raportem

```html
<iframe
  class="iframe-report"
  sandbox=""
  title="Raport tygodniowy z 6.10.2026"
  referrerpolicy="no-referrer"
  src="/api/v1/runs/{id}/report.html"
></iframe>
```

- `sandbox=""` bez `allow-scripts` i `allow-same-origin`. Obok link „Otwórz raport w nowej karcie” i przyciski pobierania (HTML/MD/JSON) — wysokości ramki nie da się dopasować do treści bez skryptu, więc `min-height` + przewijanie w ramce.
- Odpowiedź z raportem powinna nieść własny CSP (`sandbox`, `default-src 'none'`, `style-src` według raportu) — po stronie backendu; `frame-src 'self'` w CSP kokpitu.
- `.iframe-report` wymusza jasne płótno (`color-scheme: light`); raport musi ustawiać własne kolory tekstu.

### Kwoty, liczby, hash

- Kwoty: `Intl.NumberFormat("pl-PL", { style: "currency", currency: "PLN" })` daje `7 255,95 zł` z twardymi spacjami; dodaj klasy `.tabular .nowrap`; w kolumnach tabeli klasa `.num`.
- Hash SHA-256: 8 grup po 8 znaków, każda w `<span class="hash-group">`, między grupami `<wbr>` bez spacji (kopiowanie daje ciągły ciąg), całość w `<code class="hash">`. Na 375 px mieszczą się 4 grupy w wierszu (2 wiersze). Przycisk „Kopiuj całość” `.btn-secondary` z potwierdzeniem w regionie `aria-live`.
- Wynik weryfikacji: ikona + tekst („Zgodne” `.badge-ok` / „Niezgodne” `.badge-error`), nigdy sam kolor.

### Tabela

- `display: block` na mobile odbiera semantykę tabeli części czytników: dodaj `role="table"` na `<table>`, `role="rowgroup"`, `role="row"`, `role="cell"`, `role="columnheader"`/`"rowheader"`, a w `<td>` atrybut `data-label` (z tego samego słownika co nagłówek).
- Szeroką tabelę zawiń w `<div class="table-wrap" tabindex="0" role="region" aria-label="…">`.
- Sortowanie po dacie: `aria-sort` na `<th>`, przycisk w nagłówku (min 24 px).

### Dropzone

- Stan zmieniaj atrybutem `data-state`. Tekst błędu w `.field-error` pod strefą, `aria-describedby` na `<input type="file">`. Przycisk „Wybierz plik” zawsze widoczny.

## 7. Checklista a11y dla QA

Kontrasty w `tokens.css` policzono ręcznie (wzór WCAG w nagłówku pliku) — potwierdź narzędziem w obu trybach.

1. Kontrast tekstu >= 4,5:1 i UI >= 3:1 dla wszystkich par z komentarzy w `tokens.css` i `components.css` (light + dark), także na `surface`, `surface-raised`, `info-bg`, tłach statusów.
2. Fokus widoczny na każdym elemencie interaktywnym: 2 px, offset 2 px, >= 3:1 względem sąsiadujących kolorów; kolejność Tab zgodna z wizualną; brak pułapek.
3. Fokus nie jest zasłonięty (sticky nagłówek, toast) przy 375 px i przy powiększeniu 200% (2.4.11).
4. Cele: każdy element interaktywny >= 24 × 24 px (2.5.8); `.btn-cta` i `.input` >= 44 px < 640 px.
5. Status nigdy tylko kolorem (symulacja deuteranopii/skala szarości): każda odznaka, alert, błąd pola i wynik weryfikacji ma ikonę i tekst.
6. Reflow 320 px / zoom 400%: brak przewijania poziomego poza `.table-wrap`; hash łamie się na 375 px.
7. `prefers-reduced-motion: reduce`: brak pulsowania szkieletu, brak animacji toastu, przejścia natychmiastowe.
8. `forced-colors: active` (Windows High Contrast): obwódki przycisków, pól, dropzone, odznak i alertów widoczne; fokus w kolorze `Highlight`.
9. Czytnik ekranu (NVDA/VoiceOver): odznaki czytają się jako sam tekst statusu (bez „znak ptaszka”); `error-summary` ogłaszane raz; tabela mobilna zachowuje semantykę; postęp ogłaszany rozsądnie.
10. Klawiatura: dropzone i lista archiwum w pełni obsługiwane bez myszy; wybór pliku przyciskiem.
11. Brak żądań sieciowych do zewnętrznych hostów (fonty, ikony, CDN) — panel Network, CSP bez naruszeń; brak `style=""` w HTML (CSP `style-src-attr`).
12. Znaki ✓ ▲ ✕ … ? renderują się jako tekst (nie emoji) na Windows, macOS, Android, iOS.
13. Lighthouse mobile >= 90, axe 0 serious/critical (kryterium ze SPEC §3).

## 8. Trendy odrzucone

| Trend                                             | Powód                                                                          |
| ------------------------------------------------- | ------------------------------------------------------------------------------ |
| neumorfizm                                        | cienie zamiast obrysów — kontrast elementów UI poniżej 3:1                     |
| glassmorphism                                     | przezroczystość pod tekstem obniża kontrast, koszt wydajności                  |
| 3D / ciężkie animacje / parallax / scroll-jacking | zakłócają czytanie, problemy z `prefers-reduced-motion`                        |
| count-up w KPI                                    | liczba niedostępna na starcie, szkodzi czytnikom i zaufaniu do danych          |
| auto-karuzele                                     | naruszają 2.2.2 i nie pasują do rzeczowego kokpitu                             |
| KPI na pierwszym ekranie                          | brak danych u nowego użytkownika; wg UX tylko w widoku raportu (<= 3 karty)    |
| dark jako tryb domyślny                           | urzędowy ton i raporty drukowane — light domyślnie, dark z preferencji systemu |
| sekcja hero na stronach roboczych                 | zabiera ekran, opóźnia jedyną akcję (wgraj plik)                               |

## 9. Znane ograniczenia (stan 09.10.2026)

- System nie był budowany ani renderowany — brak uruchomień poleceń w tej fazie. Pierwszy build (Frontend Dev) potwierdza §5.
- Nie pokryto jeszcze: nawigacji/nagłówka aplikacji, zakładek, modalu, paginacji, checkbox/radio/switch, kopiowania w tabeli. Dodać po decyzji Frontend Dev, które ekrany ich wymagają (ustawienia, członkowie).
- Ręczna weryfikacja flow w Mobbin (warunek z `RESEARCHER_UX.md`) nie została wykonana — brak narzędzia sieciowego w tej fazie.
