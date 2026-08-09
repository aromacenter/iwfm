"use client";

/** Ikonmagyarázat a táblázatok fölé: halvány sor, amely felsorolja, melyik
 *  művelet-ikon mit jelent. */

import { useT } from "@/lib/i18n";

export default function IconLegend({ items }: { items: { icon: string; label: string }[] }) {
  const { t } = useT();
  return (
    <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
      <span className="font-medium uppercase tracking-wide">{t("common.icons")}</span>
      {items.map((item) => (
        <span key={item.icon} className="whitespace-nowrap">
          {item.icon} {item.label}
        </span>
      ))}
    </div>
  );
}
