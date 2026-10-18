import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Providers } from "@/components/Providers";
import { dictionaries, translate } from "@/i18n";
import { APP_NAME } from "@/lib/env";
import { getPreferences } from "@/lib/server/preferences";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: { default: APP_NAME, template: `%s · ${APP_NAME}` },
  description: "Raporty tydzień do tygodnia i artefakty zgodności z weryfikacją skrótu SHA-256.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "light dark",
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const { locale, theme } = await getPreferences();
  const messages = dictionaries[locale];

  return (
    <html lang={locale} data-theme={theme === "system" ? undefined : theme}>
      <body>
        <a className="skip-link" href="#main">
          {translate(messages, "app.skip")}
        </a>
        <Providers locale={locale} messages={messages}>
          {children}
        </Providers>
      </body>
    </html>
  );
}
