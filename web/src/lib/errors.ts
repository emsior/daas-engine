import { isApiError } from "./api/client";
import type { TFunction } from "@/i18n/provider";

export interface DescribedError {
  message: string;
  requestId: string | null;
  status: number | null;
}

/** Zamienia dowolny błąd na komunikat po polsku/angielsku: co poszło źle i co zrobić dalej. */
export function describeError(error: unknown, t: TFunction): DescribedError {
  if (!isApiError(error)) {
    return { message: t("error.generic"), requestId: null, status: null };
  }
  const { status } = error;
  let message: string;
  if (status === 0) message = t("error.network");
  else if (status === 401) message = t("error.401");
  else if (status === 403) message = t("error.403");
  else if (status === 404) message = t("error.404");
  else if (status === 409) message = t("error.409");
  else if (status === 413) message = t("error.413");
  else if (status === 422) message = t("error.422");
  else if (status === 429) message = t("error.429");
  else if (status >= 500) message = t("error.5xx");
  else message = t("error.generic");
  return { message, requestId: error.requestId, status };
}
