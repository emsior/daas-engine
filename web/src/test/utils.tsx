import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { AnnouncerProvider } from "@/components/Announcer";
import { dictionaries } from "@/i18n";
import { I18nProvider } from "@/i18n/provider";

function Wrapper({ children }: { children: ReactNode }) {
  return (
    <I18nProvider locale="pl" messages={dictionaries.pl}>
      <AnnouncerProvider>{children}</AnnouncerProvider>
    </I18nProvider>
  );
}

export function renderWithProviders(ui: ReactElement, options?: Omit<RenderOptions, "wrapper">) {
  return render(ui, { wrapper: Wrapper, ...options });
}
