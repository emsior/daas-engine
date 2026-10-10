"use client";

import { useId, useState } from "react";
import { Badge } from "@/components/Badge";
import { CopyButton } from "@/components/CopyButton";
import { Field } from "@/components/Field";
import { HashDisplay } from "@/components/HashDisplay";
import { Input } from "@/components/Input";
import { useT } from "@/i18n/provider";
import { InsecureContextError, hashesMatch, sha256HexOfBlob } from "@/lib/hash";

export type HashFormat = "html" | "md" | "json";
const FORMATS: HashFormat[] = ["html", "md", "json"];

type Result =
  | { status: "idle" }
  | { status: "computing"; name: string }
  | { status: "match"; name: string; hash: string; format: HashFormat }
  | { status: "mismatch"; name: string; hash: string }
  | { status: "error"; reason: "insecure" | "read" };

export function VerifyBlock({ hashes }: { hashes: Partial<Record<HashFormat, string>> }) {
  const t = useT();
  const inputId = useId();
  const [result, setResult] = useState<Result>({ status: "idle" });
  const recorded = FORMATS.flatMap((format) => {
    const hash = hashes[format];
    return hash ? [{ format, hash }] : [];
  });

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    setResult({ status: "computing", name: file.name });
    try {
      // liczone lokalnie w przeglądarce (Web Crypto): żadne żądanie sieciowe nie jest wysyłane
      const hash = await sha256HexOfBlob(file);
      const hit = recorded.find((entry) => hashesMatch(entry.hash, hash));
      setResult(
        hit
          ? { status: "match", name: file.name, hash, format: hit.format }
          : { status: "mismatch", name: file.name, hash },
      );
    } catch (error) {
      setResult({
        status: "error",
        reason: error instanceof InsecureContextError ? "insecure" : "read",
      });
    }
  };

  return (
    <section className="card stack-4" aria-labelledby={`${inputId}-title`}>
      <h2 id={`${inputId}-title`} className="card__title">
        {t("verify.title")}
      </h2>
      <p>{t("verify.lead")}</p>

      <Field id={inputId} label={t("verify.choose")} hint={t("verify.privacy")}>
        {(control) => (
          <Input
            {...control}
            type="file"
            onChange={(event) => {
              void onFile(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        )}
      </Field>

      <div aria-live="polite" className="stack-3" data-testid="verify-result">
        {result.status === "computing" ? <p>{t("verify.computing")}</p> : null}
        {result.status === "error" ? (
          <p className="field-error">
            <span>
              {result.reason === "insecure" ? t("verify.error.insecure") : t("verify.error.read")}
            </span>
          </p>
        ) : null}
        {result.status === "match" || result.status === "mismatch" ? (
          <div className="verify-result">
            <Badge
              status={result.status === "match" ? "ok" : "error"}
              label={result.status === "match" ? t("verify.match") : t("verify.mismatch")}
            />
            <p>
              {result.status === "match"
                ? t("verify.match.detail", {
                    name: result.name,
                    format: result.format.toUpperCase(),
                  })
                : t("verify.mismatch.detail", { name: result.name })}
            </p>
            <div className="hash-block">
              <span className="text-muted">{t("verify.fileHash")}</span>
              <HashDisplay hash={result.hash} />
            </div>
          </div>
        ) : null}
      </div>

      <div className="stack-3">
        <h3>{t("verify.hashes.title")}</h3>
        {recorded.length === 0 ? <p className="text-muted">{t("verify.hashes.none")}</p> : null}
        {recorded.map(({ format, hash }) => (
          <div key={format} className="hash-block">
            <span className="text-muted">
              {t("verify.hashes.format", { format: format.toUpperCase() })}
            </span>
            <HashDisplay hash={hash} />
            <div>
              <CopyButton
                value={hash}
                label={t("copy.all")}
                accessibleName={`${t("copy.all")}: ${format.toUpperCase()}`}
              />
            </div>
          </div>
        ))}
      </div>

      <details>
        <summary>{t("verify.forIt.summary")}</summary>
        <p className="mt-3">{t("verify.forIt.body")}</p>
      </details>
    </section>
  );
}
