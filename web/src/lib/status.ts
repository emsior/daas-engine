import type { BadgeStatus } from "@/components/Badge";
import type { CheckStatus, RunStatus } from "./api/types";

export function runStatusToBadge(status: RunStatus | string): BadgeStatus {
  switch (status) {
    case "SUCCESS":
      return "ok";
    case "PARTIAL":
      return "warn";
    case "FAILED":
      return "error";
    default:
      return "unknown";
  }
}

export function checkStatusToBadge(status: CheckStatus | string): BadgeStatus {
  switch (status) {
    case "PASS":
      return "ok";
    case "WARN":
      return "warn";
    case "FAIL":
    case "ERROR":
      return "error";
    case "MANUAL":
      return "pending";
    default:
      return "unknown";
  }
}
