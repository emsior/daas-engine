"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { Button } from "@/components/Button";
import { LangToggle } from "@/components/LangToggle";
import { ThemeToggle, type ThemeChoice } from "@/components/ThemeToggle";
import { useT } from "@/i18n/provider";
import { useLogout } from "@/lib/api/hooks";
import { cx } from "@/lib/cx";
import type { MessageKey } from "@/i18n";

interface NavItem {
  href: string;
  label: MessageKey;
  match: (pathname: string) => boolean;
}

const NAV: NavItem[] = [
  { href: "/", label: "nav.dashboard", match: (p) => p === "/" || p.startsWith("/runs") },
  { href: "/upload", label: "nav.upload", match: (p) => p.startsWith("/upload") },
  { href: "/health", label: "nav.health", match: (p) => p.startsWith("/health") },
  { href: "/evidence", label: "nav.evidence", match: (p) => p.startsWith("/evidence") },
  { href: "/settings", label: "nav.settings", match: (p) => p.startsWith("/settings") },
];

export function AppShell({
  orgName,
  theme,
  children,
}: {
  orgName: string | null;
  theme: ThemeChoice;
  children: ReactNode;
}) {
  const t = useT();
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const logout = useLogout();

  const handleLogout = () => {
    logout.mutate(undefined, {
      onSettled: () => {
        router.replace("/login");
      },
    });
  };

  return (
    <>
      <header className="app-header">
        <div className="layout-container app-header__bar">
          <div className="app-header__brand">
            <Link href="/" className="brand">
              {t("app.name")}
            </Link>
            <span className="text-muted app-header__org">
              {orgName ? t("header.org", { name: orgName }) : t("header.noOrg")}
            </span>
          </div>
          <Button
            variant="secondary"
            className="app-header__toggle"
            aria-expanded={menuOpen}
            aria-controls="app-menu"
            onClick={() => setMenuOpen((open) => !open)}
          >
            {t("nav.menu")}
          </Button>
          <div id="app-menu" className={cx("app-header__menu", menuOpen && "is-open")}>
            <nav aria-label={t("nav.label")}>
              <ul className="app-nav">
                {NAV.map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className="app-nav__link"
                      aria-current={item.match(pathname) ? "page" : undefined}
                      onClick={() => setMenuOpen(false)}
                    >
                      {t(item.label)}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
            <div className="app-header__tools">
              <ThemeToggle initial={theme} />
              <LangToggle />
              <Button variant="secondary" loading={logout.isPending} onClick={handleLogout}>
                {t("header.logout")}
              </Button>
            </div>
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="layout-container app-main">
        {children}
      </main>
      <Footer />
    </>
  );
}

export function Footer() {
  const t = useT();
  return (
    <footer className="app-footer">
      <div className="layout-container cluster cluster-between">
        <span className="text-muted">{t("app.footer")}</span>
        <Link href="/dpa">{t("app.footer.dpa")}</Link>
      </div>
    </footer>
  );
}
