"use client";

import { useRef } from "react";
import { useT } from "@/i18n/provider";
import { Button } from "./Button";
import { Dialog } from "./Dialog";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  pending?: boolean;
  /** Komunikat błędu operacji (np. nieudane odwołanie klucza). */
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Potwierdzenie operacji nieodwracalnej. Fokus startuje na „Anuluj” (bezpieczniejsza opcja). */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  pending,
  error,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const t = useT();
  const cancelRef = useRef<HTMLButtonElement>(null);
  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title={title}
      role="alertdialog"
      initialFocusRef={cancelRef}
      footer={
        <>
          <Button ref={cancelRef} variant="secondary" onClick={onCancel}>
            {t("app.cancel")}
          </Button>
          <Button variant="danger" loading={pending} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p>{description}</p>
      {error ? (
        <p className="field-error">
          <span>{error}</span>
        </p>
      ) : null}
    </Dialog>
  );
}
