import { describe, expect, it } from "vitest";
import { ApiError } from "./api/client";
import { dictionaries, translate } from "@/i18n";
import { describeError } from "./errors";

const t = (key: Parameters<typeof translate>[1], vars?: Parameters<typeof translate>[2]) =>
  translate(dictionaries.pl, key, vars);
const err = (status: number, code: string | null) => new ApiError({ status, title: "x", code });

describe("describeError: kody backendu mają pierwszeństwo przed statusem", () => {
  it.each([
    [409, "last_owner", "error.last_owner"],
    [429, "invitation_limit", "error.invitation_limit"],
    [410, "report_expired", "error.report_expired"],
    [422, "upload_required", "error.upload_required"],
    [429, "sse_limit", "error.sse_limit"],
  ] as const)("%i %s", (status, code, key) => {
    expect(describeError(err(status, code), t).message).toBe(t(key));
  });

  it("nieznany kod: komunikat dla statusu", () => {
    expect(describeError(err(429, "rate_limited"), t).message).toBe(t("error.429"));
  });

  it("treści PL i EN dla nowych kodów nie są puste", () => {
    for (const key of [
      "error.last_owner",
      "error.report_expired",
      "upload.failed.noRecords",
    ] as const) {
      expect(dictionaries.pl[key].length).toBeGreaterThan(10);
      expect(dictionaries.en[key].length).toBeGreaterThan(10);
    }
  });
});
