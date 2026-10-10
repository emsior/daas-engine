"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useT } from "@/i18n/provider";

export function SettingsNav() {
  const t = useT();
  const pathname = usePathname();
  const items = [
    { href: "/settings", label: t("settings.nav.general"), active: pathname === "/settings" },
    {
      href: "/settings/api-keys",
      label: t("settings.nav.keys"),
      active: pathname.startsWith("/settings/api-keys"),
    },
    {
      href: "/settings/members",
      label: t("settings.nav.members"),
      active: pathname.startsWith("/settings/members"),
    },
  ];
  return (
    <nav aria-label={t("settings.nav.label")}>
      <ul className="sub-nav">
        {items.map((item) => (
          <li key={item.href}>
            <Link href={item.href} aria-current={item.active ? "page" : undefined}>
              {item.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
