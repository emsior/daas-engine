import type { ReactNode } from "react";
import Link from "next/link";
import { LangToggle } from "@/components/LangToggle";
import { ThemeToggle, type ThemeChoice } from "@/components/ThemeToggle";
import { Footer } from "./AppShell";

/** Rama stron bez sesji lub przed onboardingiem: logo, motyw, język, wąska treść. */
export function AuthShell({
  theme,
  brand,
  children,
}: {
  theme: ThemeChoice;
  brand: string;
  children: ReactNode;
}) {
  return (
    <>
      <header className="app-header">
        <div className="layout-container app-header__bar app-header__bar--auth">
          <Link href="/" className="brand">
            {brand}
          </Link>
          <div className="app-header__tools app-header__tools--inline">
            <ThemeToggle initial={theme} />
            <LangToggle />
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="layout-container container-narrow app-main">
        {children}
      </main>
      <Footer />
    </>
  );
}
