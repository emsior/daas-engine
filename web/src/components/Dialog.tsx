"use client";

import {
  useEffect,
  useId,
  useRef,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  /** Przyciski akcji (np. Anuluj / Potwierdź). */
  footer?: ReactNode;
  /** Zamknięcie kliknięciem w tło; wyłącz dla treści pokazywanych raz (klucz API). */
  dismissOnBackdrop?: boolean;
  /** Element, który dostaje fokus po otwarciu (domyślnie pierwszy fokusowalny). */
  initialFocusRef?: RefObject<HTMLElement | null>;
  /** `alertdialog` dla potwierdzeń operacji nieodwracalnych. */
  role?: "dialog" | "alertdialog";
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusableIn(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (element) => !element.hasAttribute("hidden") && element.getAttribute("aria-hidden") !== "true",
  );
}

/**
 * Okno modalne: `aria-modal`, pułapka fokusu (Tab/Shift+Tab), zamknięcie klawiszem Escape,
 * powrót fokusu na element, który otworzył okno. Renderowane w portalu na `document.body`.
 */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  dismissOnBackdrop = true,
  initialFocusRef,
  role = "dialog",
}: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.documentElement.classList.add("dialog-open");

    const panel = panelRef.current;
    const target = initialFocusRef?.current ?? (panel ? (focusableIn(panel)[0] ?? panel) : null);
    target?.focus();

    return () => {
      document.documentElement.classList.remove("dialog-open");
      if (previouslyFocused && document.contains(previouslyFocused)) previouslyFocused.focus();
    };
  }, [open, initialFocusRef]);

  if (!open || typeof document === "undefined") return null;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onCloseRef.current();
      return;
    }
    if (event.key !== "Tab" || !panelRef.current) return;
    const items = focusableIn(panelRef.current);
    if (items.length === 0) {
      event.preventDefault();
      panelRef.current.focus();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === panelRef.current)) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first?.focus();
    }
  };

  return createPortal(
    <div
      className="dialog-backdrop"
      onMouseDown={(event) => {
        if (dismissOnBackdrop && event.target === event.currentTarget) onCloseRef.current();
      }}
    >
      <div
        ref={panelRef}
        className="dialog card"
        role={role}
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <div className="stack-4">
          <h2 id={titleId} className="dialog__title">
            {title}
          </h2>
          <div className="stack-4">{children}</div>
          {footer ? <div className="cluster dialog__footer">{footer}</div> : null}
        </div>
      </div>
    </div>,
    document.body,
  );
}
