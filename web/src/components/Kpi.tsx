import type { ReactNode } from "react";

export interface KpiProps {
  /** Etykieta tekstowa NAD liczbą (nie sam kolor ani ikona). */
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}

export function Kpi({ label, value, hint }: KpiProps) {
  return (
    <dl className="kpi">
      <dt className="kpi__label">{label}</dt>
      <dd className="kpi__value">{value}</dd>
      {hint ? <dd className="kpi__hint">{hint}</dd> : null}
    </dl>
  );
}
