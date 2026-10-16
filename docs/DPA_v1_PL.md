# Umowa powierzenia przetwarzania danych osobowych (DPA), wersja 1

**Wersja 1, do przeglądu prawnego przed użyciem produkcyjnym ⚠**

Poniższy tekst jest szkieletem i nie stanowi gotowej umowy. Kokpit pokazuje tę samą treść z pliku `web/src/content/dpa_v1_pl.md`; każdą zmianę wprowadzaj w obu plikach i podbij numer wersji (`dpa_version` organizacji zapisuje wersję zaakceptowaną przy zakładaniu konta).

## 1. Strony i definicje

Administratorem danych jest klient korzystający z usługi DaaS Engine Cloud (dalej: **Administrator**). Podmiotem przetwarzającym jest PPC, operator usługi (dalej: **Procesor**).

## 2. Przedmiot i czas trwania

Procesor przetwarza dane osobowe wyłącznie w celu świadczenia usługi: przyjęcia plików wskazanych przez Administratora, obliczenia raportów oraz ich udostępnienia. Umowa obowiązuje przez okres korzystania z usługi.

## 3. Zakres i rodzaje danych

- dane kontaktowe użytkowników organizacji (adres e-mail, imię i nazwisko, jeśli podano),
- dane zawarte w plikach wgranych przez Administratora (zalecamy, aby pliki nie zawierały danych osobowych klientów końcowych),
- dane techniczne: dzienniki zdarzeń i audyt operacji.

## 4. Obowiązki Procesora

1. Przetwarzanie wyłącznie na udokumentowane polecenie Administratora.
2. Zachowanie poufności i zobowiązanie osób upoważnionych do poufności.
3. Środki techniczne i organizacyjne zgodne z art. 32 RODO, opisane w dokumentacji bezpieczeństwa.
4. Pomoc Administratorowi w realizacji praw osób, których dane dotyczą.
5. Zgłoszenie naruszenia ochrony danych bez zbędnej zwłoki.

## 5. Podprocesorzy

Lista podprocesorów zostanie podana przed uruchomieniem produkcyjnym. Procesor informuje o każdej planowanej zmianie.

## 6. Usunięcie danych

Wgrane pliki są usuwane po upływie okresu przechowywania ustawionego w organizacji (liczonego od dnia wgrania). Pliki raportów oraz dane analityczne przebiegów starsze niż ten okres są usuwane automatycznie, cyklicznie (domyślnie co godzinę). Po usunięciu raportu zostaje rekord przebiegu z sumą kontrolną SHA-256 raportu, bez jego treści; znika on wraz z usunięciem organizacji. Kopie zapasowe wykonywane przez Procesora nie podlegają okresowi przechowywania organizacji i są usuwane zgodnie z rotacją kopii. Po zakończeniu umowy dane są usuwane lub zwracane, według wyboru Administratora.

## 7. Audyt

Procesor udostępnia informacje niezbędne do wykazania zgodności z obowiązkami i umożliwia audyty w uzgodnionym trybie.

## 8. Kontakt w sprawach ochrony danych

prochpc@gmail.com
