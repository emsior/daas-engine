"use client";

import { useId, useRef, useState, type DragEvent } from "react";
import { cx } from "@/lib/cx";
import { validateUploadFile, type UploadRejection } from "@/lib/upload-validation";

export interface DropzoneProps {
  id: string;
  /** Rozszerzenia przyjmowane przez selektor plików. */
  accept?: string;
  title: string;
  orText: string;
  buttonLabel: string;
  hint: string;
  /** Tekst o wybranym pliku (np. „Wybrany plik: raport.csv (1,2 MB)”). */
  selectedText?: string | null;
  /** Komunikat błędu pokazywany pod strefą (`aria-describedby` na polu pliku). */
  error?: string | null;
  disabled?: boolean;
  validate?: (file: File) => UploadRejection | null;
  /** Wywoływane wyłącznie dla pliku, który przeszedł walidację. */
  onFile: (file: File) => void;
  onReject: (rejection: UploadRejection, file: File) => void;
}

/**
 * Strefa wgrywania pliku. Główna akcja to przycisk „Wybierz plik” (natywne pole pliku
 * dostępne z klawiatury), przeciąganie jest dodatkiem (WCAG 2.5.7).
 * Stan wizualny przez `data-state="idle|active|error"`.
 */
export function Dropzone({
  id,
  accept = ".csv,.xlsx,.json",
  title,
  orText,
  buttonLabel,
  hint,
  selectedText,
  error,
  disabled,
  validate = validateUploadFile,
  onFile,
  onReject,
}: DropzoneProps) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const uid = useId();
  const hintId = `${uid}-hint`;
  const selectedId = `${uid}-selected`;
  const errorId = `${id}-error`;

  const describedBy = [hintId, selectedText ? selectedId : null, error ? errorId : null]
    .filter(Boolean)
    .join(" ");
  const state = error ? "error" : dragging ? "active" : "idle";

  const handle = (file: File | undefined) => {
    if (!file || disabled) return;
    const rejection = validate(file);
    if (rejection) onReject(rejection, file);
    else onFile(file);
  };

  const onDrag = (event: DragEvent<HTMLDivElement>, active: boolean) => {
    if (disabled) return;
    event.preventDefault();
    setDragging(active);
  };

  return (
    <div className="stack-2">
      <div
        className="dropzone"
        data-state={state}
        onDragEnter={(event) => onDrag(event, true)}
        onDragOver={(event) => onDrag(event, true)}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
          onDrag(event, false);
        }}
        onDrop={(event) => {
          onDrag(event, false);
          handle(event.dataTransfer.files[0]);
        }}
      >
        <p className="dropzone__title">{title}</p>
        <p className="dropzone__hint" id={hintId}>
          {hint}
        </p>
        <p className="dropzone__hint">{orText}</p>
        <label
          className={cx("btn", disabled && "pointer-events-none")}
          htmlFor={id}
          aria-disabled={disabled ? true : undefined}
        >
          {buttonLabel}
        </label>
        <input
          ref={inputRef}
          id={id}
          className="sr-only"
          type="file"
          accept={accept}
          disabled={disabled}
          aria-describedby={describedBy || undefined}
          aria-invalid={error ? true : undefined}
          onChange={(event) => {
            const file = event.target.files?.[0];
            handle(file);
            // pozwala wybrać ten sam plik jeszcze raz po poprawieniu
            event.target.value = "";
          }}
        />
        {selectedText ? (
          <p className="dropzone__hint" id={selectedId}>
            {selectedText}
          </p>
        ) : null}
      </div>
      {error ? (
        <p className="field-error" id={errorId}>
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  );
}
