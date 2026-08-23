"use client";

/** Szerződések áttekintő: minden partner-szerződés egy listában — ki, milyen
 *  gép(ek), mikortól meddig, milyen áron. Külön szűrő azokra a partnerekre,
 *  akiknél van kihelyezett gép, de nincs szerződés. */

import { useEffect, useMemo, useState } from "react";
import AppShell from "@/components/AppShell";
import PartnerInfo from "@/components/PartnerInfo";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface MachineRow {
  id: string;
  barcode: string;
  name: string;
  counter: number | null;
  customer_owned: boolean;
  deployed_at: string | null;
  product_name: string | null;
  price_per_portion: number | null;
  rent_fee: number | null;
}

interface ContractRow {
  id: string;
  partner_id: string;
  partner_name: string;
  partner_active: boolean;
  status: string;
  valid_from: string;
  valid_to: string | null;
  min_portions: number | null;
  below_min_price: number | null;
  min_kg: number | null;
  below_min_price_kg: number | null;
  rent_if_below_min: boolean;
  settlement_weeks: number;
  payment_method: string | null;
  payment_terms_days: number | null;
  note: string | null;
  machines: MachineRow[];
}

interface NoContractRow {
  partner_id: string;
  partner_name: string;
  partner_active: boolean;
  machines: MachineRow[];
}

interface Overview {
  contracts: ContractRow[];
  no_contract: NoContractRow[];
}

type Filter = "all" | "active" | "future" | "expired" | "none";

const norm = (s: string) =>
  s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");

const STATUS_STYLE: Record<string, string> = {
  active: "bg-emerald-100 text-emerald-700",
  future: "bg-sky-100 text-sky-700",
  expired: "bg-slate-200 text-slate-500",
};

const nf = new Intl.NumberFormat("hu-HU");

export default function SzerzodesekPage() {
  const { t, lang } = useT();
  const [data, setData] = useState<Overview | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [infoPartner, setInfoPartner] = useState<string | null>(null);

  useEffect(() => {
    api.get<Overview>("/api/contracts").then(setData).catch(() => {});
  }, []);

  const fmtDate = (d: string) =>
    new Date(d).toLocaleDateString(lang === "hu" ? "hu-HU" : "en-GB");

  const matches = (name: string, machines: MachineRow[]) => {
    const tokens = norm(q).split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return true;
    const hay = norm(
      [name, ...machines.flatMap((m) => [m.name, m.barcode, m.product_name ?? ""])].join(" ")
    );
    return tokens.every((tok) => hay.includes(tok));
  };

  const rows = useMemo(() => {
    if (!data) return [];
    return data.contracts.filter(
      (c) =>
        (filter === "all" || c.status === filter) && matches(c.partner_name, c.machines)
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, filter, q]);

  const orphans = useMemo(() => {
    if (!data) return [];
    return data.no_contract.filter((r) => matches(r.partner_name, r.machines));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, q]);

  const counts = useMemo(() => {
    const base = { all: data?.contracts.length ?? 0, active: 0, future: 0, expired: 0 };
    for (const c of data?.contracts ?? []) {
      if (c.status === "active") base.active += 1;
      else if (c.status === "future") base.future += 1;
      else base.expired += 1;
    }
    return { ...base, none: data?.no_contract.length ?? 0 };
  }, [data]);

  const terms = (c: ContractRow) => {
    const parts: string[] = [];
    parts.push(t("cons.dueEveryWeeks", { weeks: c.settlement_weeks || 4 }));
    if (c.payment_method) parts.push(t(`cons.payments.${c.payment_method}`));
    if (c.payment_terms_days != null)
      parts.push(t("contracts.sumTerms", { days: c.payment_terms_days }));
    if (c.min_portions != null)
      parts.push(
        t("contracts.sumMinPortions", {
          n: nf.format(c.min_portions),
          price: c.below_min_price != null ? nf.format(c.below_min_price) : "—",
        })
      );
    if (c.min_kg != null)
      parts.push(
        t("contracts.sumMinKg", {
          n: nf.format(c.min_kg),
          price: c.below_min_price_kg != null ? nf.format(c.below_min_price_kg) : "—",
        })
      );
    if (c.rent_if_below_min) parts.push(t("contracts.sumRent"));
    return parts;
  };

  const partnerBtn = (id: string, name: string, active: boolean) => (
    <button
      onClick={() => setInfoPartner(id)}
      className="text-left font-medium text-indigo-700 underline decoration-indigo-300 underline-offset-2 hover:text-indigo-900"
    >
      {name}
      {!active && (
        <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-500">
          {t("partners.inactive")}
        </span>
      )}
    </button>
  );

  const machineCell = (machines: MachineRow[]) =>
    machines.length === 0 ? (
      <span className="text-slate-400">{t("contracts.noMachines")}</span>
    ) : (
      <ul className="space-y-1">
        {machines.map((m) => (
          <li key={m.id} className="leading-snug">
            <span className="font-medium text-slate-800">{m.name}</span>{" "}
            <span className="text-xs text-slate-400">({m.barcode})</span>
            {m.customer_owned && (
              <span className="ml-1.5 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
                {t("inv.customerOwnedShort")}
              </span>
            )}
            <div className="text-xs text-slate-500">
              {m.product_name ? (
                <>
                  {m.product_name}
                  {m.price_per_portion != null && (
                    <> · <span className="font-semibold text-slate-700">{nf.format(m.price_per_portion)} {t("contracts.perPortion")}</span></>
                  )}
                </>
              ) : (
                <span className="text-slate-400">{t("contracts.noProduct")}</span>
              )}
              {m.rent_fee != null && (
                <> · {t("contracts.rentFee")}: {nf.format(m.rent_fee)} Ft/hó</>
              )}
              {m.deployed_at && <> · {t("contracts.deployedAt")}: {fmtDate(m.deployed_at)}</>}
            </div>
          </li>
        ))}
      </ul>
    );

  const FILTERS: { key: Filter; label: string; count: number }[] = [
    { key: "all", label: t("contracts.filterAll"), count: counts.all },
    { key: "active", label: t("contracts.status_active"), count: counts.active },
    { key: "future", label: t("contracts.status_future"), count: counts.future },
    { key: "expired", label: t("contracts.status_expired"), count: counts.expired },
    { key: "none", label: t("contracts.noContractFilter"), count: counts.none },
  ];

  return (
    <AppShell>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold">{t("contracts.title")}</h1>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t("contracts.searchPlaceholder")}
          className="w-72 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        />
        <div className="flex flex-wrap gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
                filter === f.key
                  ? "bg-indigo-600 text-white"
                  : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"
              }`}
            >
              {f.label} ({f.count})
            </button>
          ))}
        </div>
      </div>

      {filter === "none" ? (
        <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800">
            {t("contracts.noContractHint")}
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-slate-500">
                <th className="border-b border-slate-200 px-4 py-3">{t("cons.partner")}</th>
                <th className="border-b border-slate-200 px-4 py-3">{t("pinfo.machines")}</th>
              </tr>
            </thead>
            <tbody>
              {orphans.map((r) => (
                <tr key={r.partner_id} className="border-b border-slate-100 align-top last:border-0">
                  <td className="px-4 py-2.5">{partnerBtn(r.partner_id, r.partner_name, r.partner_active)}</td>
                  <td className="px-4 py-2.5">{machineCell(r.machines)}</td>
                </tr>
              ))}
              {orphans.length === 0 && (
                <tr><td colSpan={2} className="px-4 py-10 text-center text-slate-400">{t("contracts.listEmpty")}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase text-slate-500">
                <th className="border-b border-slate-200 px-4 py-3">{t("cons.partner")}</th>
                <th className="border-b border-slate-200 px-4 py-3">{t("contracts.validity")}</th>
                <th className="border-b border-slate-200 px-4 py-3">{t("contracts.termsCol")}</th>
                <th className="border-b border-slate-200 px-4 py-3">{t("pinfo.machines")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="border-b border-slate-100 align-top last:border-0">
                  <td className="px-4 py-2.5">{partnerBtn(c.partner_id, c.partner_name, c.partner_active)}</td>
                  <td className="whitespace-nowrap px-4 py-2.5">
                    <span className={`mr-2 rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS_STYLE[c.status] ?? ""}`}>
                      {t(`contracts.status_${c.status}`)}
                    </span>
                    {fmtDate(c.valid_from)} → {c.valid_to ? fmtDate(c.valid_to) : t("contracts.openEnded")}
                  </td>
                  <td className="px-4 py-2.5 text-slate-600">
                    {terms(c).length === 0 ? (
                      <span className="text-slate-400">{t("contracts.noTerms")}</span>
                    ) : (
                      <ul className="list-disc space-y-0.5 pl-4">
                        {terms(c).map((s, i) => (
                          <li key={i}>{s}</li>
                        ))}
                      </ul>
                    )}
                    {c.note && <div className="mt-1 text-xs italic text-slate-400">{c.note}</div>}
                  </td>
                  <td className="px-4 py-2.5">{machineCell(c.machines)}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr><td colSpan={4} className="px-4 py-10 text-center text-slate-400">{t("contracts.listEmpty")}</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <PartnerInfo partnerId={infoPartner} onClose={() => setInfoPartner(null)} />
    </AppShell>
  );
}
