/**
 * Konfiguracja zod dla przeglądarki z rygorystyczną CSP (bez `unsafe-eval`).
 * Domyślnie zod 4 sprawdza w `try { new Function("") }`, czy może generować kod (JIT). Wyjątek jest połykany,
 * ale przeglądarka i tak zgłasza naruszenie `script-src 'eval'` (zdarzenie `securitypolicyviolation`, błąd w konsoli).
 * `jitless` pomija próbę i zostaje przy wolniejszej, bezpiecznej ścieżce; dla kilku pól formularza różnicy nie widać.
 * Importuj przed pierwszym użyciem `z.parse`/`safeParse` (efekt uboczny modułu).
 */
import { z } from "zod";

z.config({ jitless: true });
